"""Run the bench receiver on a locally numbered synthetic stream and decode the saved video."""
import argparse
import gzip
import json
import os
from pathlib import Path
import subprocess
import time

import cv2

from station.bench.low_delay import receive
from tools.analysis.m3_player_timing import COLS, ROWS, collect_bitmaps, identify, marked_source, summarize


def recording_ids(path, anchor):
    capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    numbers = []
    period = COLS * ROWS
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            code = identify(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            if code is None:
                raise RuntimeError("recording contains an unreadable marker")
            if not numbers:
                index = anchor + ((code - anchor + period // 2) % period) - period // 2
            else:
                diff = (code - numbers[-1]) % period
                if not 0 < diff < period // 2:
                    raise RuntimeError("recording contains duplicate or out-of-order markers")
                index = numbers[-1] + diff
            numbers.append(index)
    finally:
        capture.release()
    if not numbers:
        raise RuntimeError("empty recording")
    return numbers


def experiment(args):
    out = args.output.resolve()
    out.mkdir()
    metrics = {}
    with marked_source(out, args.ffmpeg, args.mediamtx) as (url, stamps):
        for mode in args.modes:
            folder = out / mode
            samples = []
            rejected = 0
            def observe(frame, stamp):
                nonlocal rejected
                code = identify(frame)
                if code in stamps:
                    index, sent = stamps[code]
                    samples.append((index, sent, stamp))
                else:
                    rejected += 1
            env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_VIDEO_DRIVER="dummy",
                       SDL_VIDEO_DUMMY_SAVE_FRAMES="1", SDL_RENDER_DRIVER="software")
            started = time.time_ns()
            before = (out / "server.log").read_text(errors="replace").count("is reading from path")
            result = receive(url, folder, args.ffmpeg.resolve(), args.ffplay.resolve(), args.seconds,
                             mode == "drop", diagnostic=True, env=env, tick=lambda: collect_bitmaps(folder, observe))
            readers = (out / "server.log").read_text(errors="replace").count("is reading from path") - before
            age = summarize(samples, started, rejected)
            rows = age.pop("rows")
            recorded = recording_ids(folder / "recording.ts", samples[0][0])
            replay_matches_ts = result["mp4_ready"] and recording_ids(folder / "recording.mp4", samples[0][0]) == recorded
            scored = [i for i in recorded if rows[0][0] <= i <= rows[-1][0]]
            scored_set = set(scored)
            if not all(row[0] in scored_set for row in rows):
                raise RuntimeError("display contains frame numbers missing in the recording")
            with (folder / "decode.log").open("wb") as log:
                decode = subprocess.run([args.ffmpeg.resolve(), "-nostdin", "-hide_banner", "-loglevel", "error",
                                         "-xerror", "-i", folder / "recording.ts", "-map", "0:v:0",
                                         "-f", "null", "-"], stdout=log, stderr=log, check=False)
            result.update(display=age, readers=readers, replay_decode_exit=decode.returncode, mp4_matches_ts=replay_matches_ts,
                          recorded_frames=len(recorded), recorded_first=recorded[0], recorded_last=recorded[-1],
                          recording_missing=recorded[-1]-recorded[0]+1-len(recorded),
                          recorded_in_display_window=len(scored),
                          displayed_pct=round(100*len(rows)/len(scored), 3))
            (folder / "display.json").write_text(json.dumps(rows), encoding="utf-8")
            (folder / "recorded-ids.json").write_text(json.dumps(recorded), encoding="utf-8")
            result["single_reader_replay_passed"] = (
                readers == 1 and result["reason"] == "deadline" and result["recorder_exit"] == 0
                and result["player_exit"] == 0 and decode.returncode == 0 and replay_matches_ts and not result["inspection_stopped"]
                and result["rtp_discontinuities"] == 0 and result["recording_missing"] == 0
                and (args.seconds < 305 or min(age["span_s"], result["arrival"]["longest_s"]) >= 300)
            )
            metrics[mode] = result
            (out / "summary.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            print(mode, result, flush=True)
    return all(m["single_reader_replay_passed"] for m in metrics.values())


def collect_evidence(paths, output):
    """Verify the two run folders and package only numeric samples and aggregate results."""
    archive = {}
    for path in paths:
        for mode, result in json.loads((path / "summary.json").read_text()).items():
            data = {key: json.loads((path / mode / (key + ".json")).read_text())
                    for key in ("arrival", "display", "recorded-ids")}
            arrivals, display, ids = data["arrival"], data["display"], data["recorded-ids"]
            scored = set(i for i in ids if display[0][0] <= i <= display[-1][0])
            observed = [i for i, _, _ in display]
            if (not all(i in scored for i in observed) or len(display) != result["display"]["frames"]
                    or len(scored) != result["recorded_in_display_window"] or len(ids) != result["recorded_frames"]
                    or len(arrivals) != result["arrival"]["frames"]
                    or (ids[-1] - ids[0] + 1 - len(ids)) != result["recording_missing"]):
                raise ValueError("sample and summary mismatch")
            archive[f"{path.name}/{mode}"] = dict(summary=result, **data)
    raw = json.dumps(archive, separators=(",", ":")).encode()
    with output.open("xb") as sink:
        sink.write(gzip.compress(raw, mtime=0))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "ffmpeg", "mediamtx", "ffplay"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--modes", nargs="+", choices=("drop", "keep"), default=["drop", "keep"])
    parser.add_argument("--seconds", type=int, default=20)
    raise SystemExit(int(not experiment(parser.parse_args())))
