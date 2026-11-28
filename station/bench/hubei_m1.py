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


def prepare(run, v18=False):
    directory = "/sdcard/hcv"
    if v18:
        from station.bench import m1_v18
        m1_v18.prepare(run, combined=True)
        directory += "/m1_v18"
    else:
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
        f'config = load_config({directory!r})\n'
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
    start, end = capture.get("display_start_epoch"), capture.get("display_end_epoch")
    return during_interval(lines, start, end, 60)


def during_interval(lines, start, end, fps):
    rows = windows(lines)
    if start is None or end is None:
        return None
    anchors = []
    for label in ("HCV_DUAL_ORIGIN,", "HCV_DUAL_END,"):
        marks = [line.split(label, 1) for line in lines if label in line]
        if len(marks) != 1:
            return None
        prefix, raw = marks[0]
        anchors.append((datetime.fromisoformat(prefix.strip()).timestamp(), int(raw.split(",")[-1])))
    (host0, board0), (host1, board1) = anchors
    if host1 <= host0 or board1 <= board0:
        return None
    # Only endpoint markers are sent live. Use the envelope of both clock
    # offsets so an uncertain boundary window cannot increase the overlap.
    offsets = [host - board / 1e6 for host, board in anchors]
    inside = [row for row in rows if start <= min(offsets) + (row[2] - row[3]) / 1e6
              and max(offsets) + row[2] / 1e6 <= end]
    if not inside:
        return None
    result = metrics(inside, fps)
    result["anchor_offset_difference_s"] = max(offsets) - min(offsets)
    result["serial_span_s"] = host1 - host0
    result["process_p95_within_6ms"] = result["process_us"][1] <= 6000
    return result


def wizard(args, ask=input, *, v18_visit=None):
    evidence = prior_ap(args.runs) if v18_visit is None else v18_visit
    vlc, _, _ = bench.tools()
    ffmpeg = shutil.which("ffmpeg") or bench.HERE / "runtime/ffmpeg.exe"
    original = wlan.snapshot()
    if (not original or not original[2] or original[1] == bench.config_wifi()["ssid"]
            or not Path(ffmpeg).is_file() or not bench.find_ffprobe()):
        raise RuntimeError("开始前请确认电脑连着可恢复的日常 Wi-Fi，且录像工具齐全。")
    plan = dict(hubei.plan("ap"), id="hubei-m1", title="湖北图传同时找球", seconds=66, m1=True)
    suffix = "-hubei-m1"
    if v18_visit is not None:
        plan["v18_combined"] = True
        suffix = "-v18-hubei-m1"
    run = bench.Run(args.runs / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + suffix), plan, False)
    if v18_visit is None:
        run.summary["prior_ap"] = evidence.parent.parent.name
    else:
        run.summary["visit"] = v18_visit.name
    run.summary.update(scope="本轮只测时序。1080p60 的视场和裁剪可能改变，VALID 和位置误差不参与判定。",
                       next_if_pass="保持湖北线程结构，仅改传感器为 720p90，按 90 fps 再测 60 秒。两轮通过后确认并行。")
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
        passed = measured and restored
        message = "已取得同步测量" if measured else "未取得完整的同步测量"
        if v18_visit is not None and measured and not sample["process_p95_within_6ms"]:
            passed = False
            message = "同步测量完成，找球处理 p95 超过 6 ms。"
        run.summary.update(passed=passed, message=message)
        run.finish()
        print(run.summary["message"], run.directory)
    return int(not passed)
