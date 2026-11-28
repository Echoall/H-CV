"""Run two separate v1.8 AP sessions using the same board setup and different PC receivers."""
import os

from station.bench import bench, hubei, low_delay, wlan
from station.bench.field import frame_numbers


def capture(run, vlc, url, plan, simulated):
    cap = run.summary["capture"]
    diagnostic = plan["rtp_diagnostic"]
    if plan["receiver_ab"] == "vlc200":
        with low_delay.receiver_path(url, run.directory, diagnostic, cap) as local:
            bench.receive(run, vlc, local, plan, simulated)
        return

    stage = run.summary["stages"]["single-receiver"] = {"started": bench.now()}
    folder = run.directory / "single"
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_VIDEO_DRIVER="dummy",
               SDL_RENDER_DRIVER="software") if simulated else None
    result = low_delay.receive(url, folder, *low_delay.binaries(), plan["seconds"] + 2, plan["framedrop"],
                               diagnostic=diagnostic, env=env)
    stage["ended"] = bench.now()
    received = result["arrival"]["frames"] if diagnostic else result["mp4_ready"]
    cap.update(result, transport_observed=["tcp"] if received else [],
               exit_code=result["recorder_exit"])
    if result["reason"] != "deadline" or result["player_exit"] != 0:
        cap["client_end_reason"] = result["reason"] if result["reason"] != "deadline" else "player_error"
    recording = folder / "recording.mp4"
    if recording.is_file() and recording.stat().st_size:
        cap.update(bench.decode(recording, run.directory / "preview.png"))
        bench.inspect_frame_rate(run, recording, bench.find_ffprobe())


def evaluate(run, clean, ffmpeg):
    cap = run.summary["capture"]
    path = (run.directory / "recording.mp4" if run.summary["plan"]["receiver_ab"] == "vlc200"
            else run.directory / "single/recording.mp4")
    if not path.is_file() or not cap.get("frames"):
        return False
    cap["frame_numbers"] = frame_numbers(path, ffmpeg)
    code, _, _ = run.command("replay-decode", [ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i",
                                               str(path), "-map", "0:v:0", "-f", "null", "-"], timeout=45)
    diagnostic = run.summary["plan"]["rtp_diagnostic"]
    path_verified = cap.get("receiver_path") == ("rtp_relay" if diagnostic else "direct")
    arrival_ok = (not diagnostic or (not cap.get("inspection_stopped") and cap.get("rtp_discontinuities") == 0
                                     and cap.get("arrival", {}).get("continuous_60s")))
    return bool(clean and cap.get("exit_code") == 0 and not cap.get("client_end_reason")
                and cap.get("transport_observed") == ["tcp"] and path_verified and arrival_ok
                and cap.get("video_track_seconds", 0) >= 58 and (cap.get("width"), cap.get("height")) == (640, 360)
                and cap["frame_numbers"]["reference_pictures"] > 0 and cap["frame_numbers"]["gap_pct"] <= 1
                and code == 0)


def wizard(args, ask=input):
    simulated = bool(args.simulate)
    vlc, ffmpeg, server = bench.tools(*(p.resolve() for p in (args.simulate or ())))
    receiver_ffmpeg, _ = low_delay.binaries()
    ffmpeg = ffmpeg or receiver_ffmpeg
    root = args.runs.resolve() / (bench.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-players")
    root.mkdir(parents=True)
    outcomes = {}
    for name in ("vlc200", "ffplay"):
        if not simulated and ask(f"准备进行 {name} 独立测试，回车继续，q 结束 > ").strip().lower() == "q":
            break
        original = None if simulated else wlan.snapshot()
        if not simulated and (not original or not original[2] or original[1] == bench.config_wifi()["ssid"]):
            raise RuntimeError("请先连接可恢复的日常 Wi-Fi。")
        plan = dict(hubei.plan("ap"), id="v18-" + name, receiver_ab=name, firmware_compare=True,
                    framedrop=args.framedrop, rtp_diagnostic=args.rtp_diagnostic)
        run = bench.Run(root / name, plan, simulated)
        passed = False
        restored = False
        try:
            if simulated:
                with bench.localhost_source(ffmpeg, server, run.directory, ("tcp",), size="640x360",
                                            fps=60, gop=10, bitrate_kbps=1000) as url:
                    clean = bench.stream(run, bench.simulated_board(url, 68), None, plan, {}, None, vlc, True)
            else:
                clean = hubei.physical(run, "ap", ask, original, vlc)
            passed = evaluate(run, clean, ffmpeg)
        finally:
            try:
                restored = simulated or wlan.restore(original, diagnostics=run.summary["network"])
            finally:
                run.summary["network"]["restored"] = restored
                run.summary["passed"] = outcomes[name] = passed and restored
                run.finish()
                bench.write_json(root / "comparison.json", outcomes)
        if not restored or not clean:
            break
    print("接收器对照结果", outcomes, root)
    return int(len(outcomes) != 2 or not all(outcomes.values()))
