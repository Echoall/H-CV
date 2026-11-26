"""Measure simultaneous display and CHN1 detection after a physical AP-only pass."""

from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess

from station.bench import bench, hubei, wlan


def prior_ap(runs):
    for path in sorted(runs.glob("*-hubei/ap/summary.json"), reverse=True):
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record.get("simulation") is False and record.get("plan", {}).get("id") == "hubei-ap"
                and record.get("result") == "通过" and record.get("passed") is True
                and record.get("network", {}).get("restored") is True
                and (path.parent / "recording.mp4").is_file() and any(path.parent.glob("latency-*.png"))):
            return path
    raise RuntimeError("请先完成湖北纯图传实板 AP 轮。已有通过记录后，这个向导才会开始。")


def prepare(run):
    for relative, target in [("k230/main_hcv.py", "hubei_m1_runner.py"),
                             ("k230/hcv/hubei_detection.py", "hcv/hubei_detection.py")]:
        code, _, _ = run.command("prepare-" + Path(target).stem, bench.BOARD + [
            "put", str(bench.ROOT / relative), "/sdcard/hcv/" + target], timeout=30)
        if code != 0:
            raise RuntimeError("准备并行检测模块失败，详见本轮记录。")
    settings = json.loads((bench.HERE / "hubei_detection.json").read_text(encoding="utf-8"))
    return (
        'import sys\nsys.path.insert(0, "/sdcard/hcv")\n'
        'from hcv.config import load_config\nfrom hcv.hubei_detection import HubeiDetection\n'
        'config = load_config("/sdcard/hcv")\n'
        'if not config["detector"]["params"].get("m1_fast"): raise ValueError("M1 fast config required")\n'
        'config["sensor"].update(width=SENSOR_WIDTH, height=SENSOR_HEIGHT, fps=SENSOR_FPS)\n'
        f'detection = HubeiDetection(config, {settings!r})\n'
    )


def windows(lines):
    rows = []
    for line in lines:
        prefix, sep, raw = line.strip().partition("HCV_DUAL,")
        if sep:
            stamp = datetime.fromisoformat(prefix.strip()).timestamp() if prefix.strip() else None
            count, elapsed, duration, *quantiles = map(int, raw.split(","))
            if len(quantiles) != 6 or count != (len(rows) + 1) * 60 or duration <= 0:
                raise ValueError("incomplete M1 timing windows")
            previous = rows[-1][2] if rows else 0
            if elapsed - previous != duration:
                raise ValueError("inconsistent M1 elapsed time")
            rows.append((stamp, count, elapsed, duration, *quantiles))
    return rows


def metrics(rows, fps):
    duration = sum(row[3] for row in rows)
    count = len(rows) * 60
    return dict(frames=count, wall_us=duration, source_fps=fps,
                estimated_drops=max(0, duration * fps // 1_000_000 - count),
                process_us=[max(row[i] for row in rows) for i in (4, 5, 6)],
                after_capture_us=[max(row[i] for row in rows) for i in (7, 8, 9)])


def during_display(lines, capture):
    rows = windows(lines)
    start, end = capture.get("display_start_epoch"), capture.get("display_end_epoch")
    if start is None or end is None:
        return None
    inside = [row for row in rows if start <= row[0] <= end]
    if len(inside) < 2:
        return None
    if any(b[0] <= a[0] or b[1] != a[1] + 60 for a, b in zip(inside, inside[1:])):
        raise ValueError("non-contiguous serial timestamps in display window")
    result = metrics(inside[1:], 60)
    result["serial_span_s"] = inside[-1][0] - inside[0][0]
    result["process_p95_within_6ms"] = result["process_us"][1] <= 6000
    return result


def wizard(args, ask=input):
    evidence = prior_ap(args.runs)
    vlc, _, _ = bench.tools()
    ffmpeg = shutil.which("ffmpeg") or bench.HERE / "runtime/ffmpeg.exe"
    original = wlan.snapshot()
    if (not original or not original[2] or original[1] == bench.config_wifi()["ssid"]
            or not Path(ffmpeg).is_file() or not bench.find_ffprobe()):
        raise RuntimeError("开始前请确认电脑连着可恢复的日常 Wi-Fi，且录像工具齐全。")
    plan = dict(hubei.plan("ap"), id="hubei-m1", title="湖北图传同时找球", seconds=66, m1=True)
    run = bench.Run(args.runs / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-hubei-m1"), plan, False)
    run.summary["prior_ap"] = evidence.parent.parent.name
    measured = False
    try:
        clean = hubei.physical(run, "ap", ask, original, vlc)
        video = hubei.assess(run, clean, ffmpeg)
        log = run.directory / "board.serial.log"
        sample = during_display(log.read_text(encoding="utf-8").splitlines(), run.summary["capture"])
        measured = bool(video == "通过" and sample and sample["wall_us"] >= 60_000_000
                        and sample["serial_span_s"] >= 60 and run.summary["capture"].get("video_track_seconds", 0) >= 60)
        run.summary.update(video_result=video, m1_during_display=sample)
    finally:
        try:
            restored = wlan.restore(original, diagnostics=run.summary["network"])
        except (OSError, subprocess.TimeoutExpired):
            restored = False
        run.summary["network"]["restored"] = restored
        if not restored:
            print(wlan.MANUAL_RESTORE)
        run.summary.update(passed=measured and restored,
                           message="已取得同步测量" if measured else "未取得完整的同步测量")
        run.finish()
        print(run.summary["message"], run.directory)
    return int(not (measured and restored))
