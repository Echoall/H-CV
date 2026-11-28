"""S4 CHN0 recording and CHN1 timing over a common host observation interval."""
import re
import subprocess
import sys

from station.bench import bench, hubei, hubei_m1, player_compare, wlan
from station.bench.low_delay import binaries


MODES = ((1920, 1080, 60), (1280, 720, 90))


def assess(run, clean, ffmpeg, mode, lines):
    video = player_compare.evaluate(run, clean, ffmpeg)
    cap = run.summary["capture"]
    received = cap.get("arrival", {})
    start, end = received.get("start_ns"), received.get("end_ns")
    sample = hubei_m1.during_interval(lines, start / 1e9, end / 1e9, mode[2]) if start and end else None
    pattern = rf"find sensor gc2093.*output {mode[0]}x{mode[1]}@{mode[2]}\b"
    selected = any(re.search(pattern, line, re.IGNORECASE) for line in lines)
    run.summary.update(video_passed=video, detection_during_receive=sample,
                       driver_mode_verified=selected if not run.summary["simulation"] else None)
    return bool(video and (selected or run.summary["simulation"]) and sample and sample["wall_us"] >= 60_000_000
                and sample["process_p95_within_6ms"] and not sample["estimated_drops"])


def run_mode(root, mode, ask, simulation=None):
    if mode not in MODES:
        raise ValueError("unexpected S4 mode")
    vlc, ffmpeg, server = bench.tools(*(p.resolve() for p in (simulation or ())))
    ffmpeg = ffmpeg or binaries()[0]
    plan = dict(hubei.plan("ap"), id="s4-" + str(mode[2]), title="边传视频边找球", seconds=68, m1=True,
                sensor_mode=mode, receiver_ab="ffplay", rtp_diagnostic=True, framedrop=False, v18_combined=True)
    run = bench.Run(root / plan["id"], plan, bool(simulation))
    original = None if simulation else wlan.snapshot()
    if not simulation and (not original or not original[2] or original[1] == bench.config_wifi()["ssid"]):
        raise RuntimeError("请先连接可恢复的日常 Wi-Fi。")
    passed = restored = False
    log = run.directory / "board.serial.log"
    try:
        if simulation:
            log = run.directory / "history-detection.log"
            with bench.localhost_source(ffmpeg, server, run.directory, ("tcp",), size="640x360", fps=60,
                                        gop=10, bitrate_kbps=1000) as url:
                cmd = [sys.executable, "-m", "tools.analysis.hubei_m1_replay", str(log), "--seconds", "82",
                       "--fps", str(mode[2]), "--target", "--host-timestamps"]
                with subprocess.Popen(cmd, cwd=bench.ROOT) as replay:
                    try:
                        clean = bench.stream(run, bench.simulated_board(url, 70), None, plan, {}, None, vlc, True)
                        if replay.wait(timeout=30):
                            raise RuntimeError("检测回放未正常结束。")
                    finally:
                        if replay.poll() is None:
                            replay.kill()
        else:
            clean = hubei.physical(run, "ap", ask, original, vlc)
        passed = assess(run, clean, ffmpeg, mode, log.read_text(encoding="utf-8").splitlines())
    finally:
        try:
            restored = bool(simulation) or wlan.restore(original, diagnostics=run.summary["network"])
        finally:
            run.summary["network"]["restored"] = restored
            run.summary["passed"] = passed and restored
            run.finish()
    return int(not run.summary["passed"])
