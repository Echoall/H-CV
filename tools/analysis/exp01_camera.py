"""Capture and summarize P0-EXP01 on the K230."""

import argparse
import csv
from itertools import product
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "tools" / "board" / "board.py"
PROBE = ROOT / "k230" / "exp" / "exp01_camera_modes.py"
MODES = ((1280, 720, 90), (1280, 960, 60), (1920, 1080, 60), (1920, 1080, 30))
SIZES = ((640, 360), (640, 480), (320, 180))
COOLDOWN = 300


def quantiles(values, key):
    ordered = sorted(values)
    return {
        key + label: ordered[max(0, math.ceil(len(ordered) * portion) - 1)]
        for label, portion in (("p50", .50), ("p95", .95), ("p99", .99), ("max", 1))
    }


def summarize(raw):
    events, frames = {}, []
    for line in raw.splitlines():
        if line.startswith("HCV_EXP01 "):
            entry = json.loads(line.partition(" ")[2])
            events[entry["event"]] = entry
        elif line.startswith("HCV_FRAME,"):
            index, end, duration = map(int, line.split(",")[1:])
            frames.append((index, end, duration))

    result = {}
    if "mode" in events:
        result.update({key: value for key, value in events["mode"].items() if key != "event"})
    if "unsupported" in events:
        return dict(result, status="unsupported", detail=events["unsupported"]["reason"])
    if "cleanup_error" in events or "error" in events:
        error = events.get("cleanup_error", events.get("error"))
        return dict(result, status="error", detail=error["detail"], frames=len(frames))
    expected = events.get("done", {}).get("expected")
    if len(frames) < 2 or len(frames) != expected or "image" not in events:
        return dict(result, status="incomplete", frames=len(frames))
    if any(index != expected_index for expected_index, (index, _, _) in enumerate(frames)):
        return dict(result, status="incomplete", detail="missing or out-of-order frames",
                    frames=len(frames))

    intervals = [b[1] - a[1] for a, b in zip(frames, frames[1:])]
    if min(intervals) <= 0:
        return dict(result, status="error", detail="non-increasing frame timestamps")
    result.update(status="ok", frames=len(frames), fps=len(intervals) * 1e6 / sum(intervals))
    result.update(quantiles(intervals, "interval_"))
    result.update(quantiles([frame[2] for frame in frames], "snapshot_"))
    result["image"] = {key: value for key, value in events["image"].items() if key != "event"}
    if "image_apis" in events:
        result["image_apis"] = events["image_apis"]
    return result


def cases():
    for mode, size, stream, crop in product(MODES, SIZES, (False, True), (False, True)):
        yield (*mode, *size, stream, crop)


def key(case):
    w, h, fps, x, y, stream, crop = case
    return f"{w}x{h}-{fps}_{x}x{y}_chn1-{int(stream)}_crop-{int(crop)}"


def capture(port, output, frames, smoke=False):
    output.mkdir(parents=True, exist_ok=False)
    combinations = list(cases())[:1] if smoke else list(cases())
    original = PROBE.read_text(encoding="utf-8")
    previous = None
    failures = 0
    with tempfile.TemporaryDirectory(prefix="hcv-exp01-") as directory:
        script = Path(directory) / "capture.py"
        for case in combinations:
            if previous is not None:
                remaining = COOLDOWN - (time.monotonic() - previous)
                while remaining > 0:
                    time.sleep(min(60, remaining))
                    remaining = COOLDOWN - (time.monotonic() - previous)
            case_id = key(case)
            program = (f"HCV_CASE = {case!r}\nHCV_FRAMES = {frames}\n"
                       f"HCV_IMAGE_API_PROBE = {smoke!r}\n" + original)
            script.write_text(program,
                              encoding="utf-8")
            raw_log = output / (case_id + ".log")
            command = [sys.executable, str(BOARD), "--port", port, "run", str(script),
                       "--timeout", "120", "--stream", "--log", str(raw_log)]
            try:
                run = subprocess.run(command, capture_output=True, timeout=150)
            except subprocess.TimeoutExpired as error:
                result = {"status": "host-timeout"}
                (output / (case_id + ".stderr")).write_bytes(error.stderr or b"")
                failures += 1
            else:
                if run.stderr:
                    (output / (case_id + ".stderr")).write_bytes(run.stderr)
                data = raw_log.read_text(encoding="utf-8", errors="replace") if raw_log.exists() else ""
                if run.returncode:
                    if not data:
                        (output / (case_id + ".stdout")).write_bytes(run.stdout)
                    result = dict(summarize(data), status="board-command-failed",
                                  exit_code=run.returncode)
                    failures += 1
                else:
                    result = summarize(data)
                    failures = 0
            previous = time.monotonic()
            result["case"] = list(case)
            (output / (case_id + ".json")).write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(case_id, result["status"], result.get("fps", ""), flush=True)
            if failures >= 3 or (smoke and result["status"] != "ok"):
                return 1
    return 0


def write_summary(directory):
    cases = [(file.stem, json.loads(file.read_text(encoding="utf-8")))
             for file in sorted(directory.glob("*.json"))]
    headers = ["case", "status", "detail", "frames", "fps",
               "interval_p50", "interval_p95", "interval_p99", "interval_max",
               "snapshot_p50", "snapshot_p95", "snapshot_p99", "snapshot_max"]
    with (directory / "summary.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        for label, case in cases:
            row = {field: case.get(field, "") for field in headers}
            row["case"] = label
            writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "summary"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", default="auto")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.command == "summary":
        write_summary(args.output)
        return 0
    result = capture(args.port, args.output, 30 if args.smoke else 1000, args.smoke)
    write_summary(args.output)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
