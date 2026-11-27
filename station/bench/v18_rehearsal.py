"""Exercise the Windows visit tools against a loopback camera and archived M1 pixels."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from station.bench import bench, hubei, hubei_m1, m1_v18


def play(directory, url, tools, seconds):
    vlc, ffmpeg, _ = tools
    plan = hubei.plan("ap")
    # receive() gives a direct client two extra seconds for rendered display samples.
    plan["seconds"] = seconds - 2
    run = bench.Run(directory, plan, True)
    run.summary["rtsp_ready_at"] = bench.now()
    try:
        cap = bench.receive(run, vlc, url, plan, True)
        if cap.get("frames"):
            cap["frame_numbers"] = hubei.frame_numbers(directory / "recording.mp4", ffmpeg)
        run.summary["passed"] = (cap.get("exit_code") == 0 and not cap.get("client_end_reason")
                                 and cap.get("transport_observed") == ["tcp"]
                                 and (cap.get("width"), cap.get("height")) == (640, 360)
                                 and cap.get("continuous_display_s", 0) >= seconds - 1
                                 and cap.get("video_track_seconds", 0) >= seconds - 2
                                 and cap.get("frame_numbers", {}).get("gap_pct", 100) <= 1)
        return run.summary["passed"], cap
    finally:
        run.finish()


def rehearse(root, tools):
    _, ffmpeg, server = tools
    root.mkdir(parents=True)
    report = {"simulation": True, "source": "localhost synthetic video, archived K230 ROI pixels"}
    try:
        with bench.localhost_source(ffmpeg, server, root, ("tcp",), size="640x360", fps=60,
                                    gop=10, bitrate_kbps=1000) as url:
            sessions = []
            report["connections"] = sessions
            for name in ("first", "second"):
                ok, cap = play(root / name, url, tools, 20)
                sessions.append(dict(name=name, passed=ok, frames=cap.get("frames"),
                                     display_s=cap.get("continuous_display_s"),
                                     video_s=cap.get("video_track_seconds")))
                disconnected = time.monotonic()
                time.sleep(10)
                sessions[-1]["without_client_s"] = round(time.monotonic() - disconnected, 3)

            metrics = m1_v18.assess(bench.ROOT / "reports/data/M1/pool-ref-longrun.log")
            report["archived_m1"] = dict(frames=metrics["summary"]["frames"],
                                         p95_us=metrics["max_sample_process_p95_us"],
                                         active_gc_drops=metrics["duration_dropped_including_gc"],
                                         measured_on="v1.4")
            log = root / "detection.log"
            cmd = [sys.executable, "-m", "tools.analysis.hubei_m1_replay", str(log),
                   "--seconds", "92", "--fps", "60", "--target", "--host-timestamps"]
            with (root / "detection.stderr.log").open("wb") as err, subprocess.Popen(
                    cmd, cwd=bench.ROOT, stderr=err) as proc:
                try:
                    ok, cap = play(root / "combined", url, tools, 68)
                    code = proc.wait(timeout=100)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
            if code != 0:
                raise RuntimeError("电脑端 M1 回放提前退出。")
            sample = hubei_m1.during_display(log.read_text(encoding="utf-8").splitlines(), cap)
            report.update(combined_video_passed=ok, simultaneous=sample)
            report["overlap_measured"] = bool(ok and sample and sample["wall_us"] >= 60_000_000)
            report["passed"] = bool(all(session["passed"] for session in sessions)
                                    and report["overlap_measured"] and sample["process_p95_within_6ms"])
    finally:
        bench.write_json(root / "rehearsal.json", report)
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("ffmpeg", type=Path)
    parser.add_argument("mediamtx", type=Path)
    args = parser.parse_args()
    ok = rehearse(args.output.resolve(), bench.tools(args.ffmpeg.resolve(), args.mediamtx.resolve()))
    print(json.dumps({"passed": ok, "directory": str(args.output)}, ensure_ascii=False))
    raise SystemExit(int(not ok))
