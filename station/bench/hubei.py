"""Operator-led reproduction of the Hubei team's threaded, video-only RTSP stream."""

import getpass
import json
from pathlib import Path
import shutil
import subprocess
import time

from station.bench import bench, power, wlan
from station.bench.field import frame_numbers


def plan(mode):
    return dict(id="hubei-" + mode, title="湖北 RTSP " + mode.upper(), seconds=60,
                hubei=True, direct_rtsp=True, latency=True, transport="tcp", caching_ms=200,
                width=640, height=360, fps=60, gop=10, bitrate_kbps=1000)


def conclusion(results):
    if results == ["通过"]:
        return "板载 AP 通过。优先检验程序结构或负载，下轮保留线程结构，仅加回 CHN1。"
    if results == ["失败", "通过"]:
        return "仅 STA 通过。优先采用随身 Wi-Fi，继续核实板载 AP 路径。"
    if results == ["失败", "失败"]:
        return "本板与当前固件在两种网络下都未通过。交由负责人决定改用 v1.8 或树莓派图传。"
    return "对照尚不完整，请查看各轮记录。"


def verify_power(run, ask):
    power.reconnect(ask)
    if run.command("inventory", bench.BOARD + ["info"], timeout=18)[0]:
        raise RuntimeError("USB 已枚举，但板端未响应 info。")
    probe = bench.ROOT / "tools/board/m3_hubei_inventory.py"
    code, out, _ = run.command("hubei-api", bench.BOARD + ["run", str(probe), "--timeout", "8"], timeout=20)
    if code or "HCV_HUBEI_API v1.4-a7de1c8 1" not in out:
        raise RuntimeError("固件版本或 Python 接口核对未通过。")


def saved_sta():
    path = bench.HERE / "config.local.json"
    if not path.is_file():
        return None
    settings = json.loads(path.read_text(encoding="utf-8"))
    wifi = settings.get("hubei_sta")
    if isinstance(wifi, dict):
        ssid, password = wifi.get("ssid"), wifi.get("password")
        if (isinstance(ssid, str) and 1 <= len(ssid.encode("utf-8")) <= 32
                and isinstance(password, str) and 8 <= len(password) <= 63):
            return dict(mode="sta", ssid=ssid, password=password)
    return None


def sta_wifi(ask):
    wifi = saved_sta()
    if not wifi:
        ssid = ask("请输入随身 Wi-Fi 的名称，直接回车退出 > ").strip()
        if not ssid:
            raise RuntimeError("STA 未配置")
        password = getpass.getpass("随身 Wi-Fi 密码（输入不显示） > ")
        wifi = dict(ssid=ssid, password=password)
        if not (1 <= len(ssid.encode("utf-8")) <= 32 and 8 <= len(password) <= 63):
            raise RuntimeError("随身 Wi-Fi 的名称或密码长度有误，配置未保存。")
        path = bench.HERE / "config.local.json"
        settings = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        settings["hubei_sta"] = wifi
        bench.write_json(path, settings)
    return dict(mode="sta", ssid=wifi["ssid"], password=wifi["password"])


def physical(run, mode, ask, original, vlc):
    wifi = bench.config_wifi() if mode == "ap" else sta_wifi(ask)
    run.private.update((wifi["ssid"], wifi["password"], *filter(None, original)))
    if mode == "sta" and wlan.snapshot() != original:
        raise RuntimeError("进入第二轮前尚未恢复原 Wi-Fi。")
    if mode == "sta" and original[1] != wifi["ssid"]:
        ask("请把电脑连到随身 Wi-Fi，与板子共用同一个网络。连好后按回车 > ")
        current = wlan.snapshot()
        if not current or current[1] != wifi["ssid"]:
            raise RuntimeError("电脑未确认连接指定的随身 Wi-Fi。")
    verify_power(run, ask)
    combined = run.summary["plan"].get("m1", False)
    if combined:
        from station.bench.hubei_m1 import prepare
        entry = prepare(run)
    if ask("镜头对准电脑屏幕左侧后输入「拍」，随后五低一高启动本轮 > ").strip() != "拍":
        raise RuntimeError("用户取消拍摄")
    import winsound
    for _ in range(5):
        winsound.Beep(440, 150)
        time.sleep(0.85)
    winsound.Beep(1100, 200)
    script = (bench.ROOT / "tools/board/m3_hubei_rtsp.py").read_text(encoding="utf-8")
    if combined:
        source = (script + "\n" + entry + "\nrun(%r, detection)\n" % wifi).encode()
    else:
        source = (f"HCV_HUBEI_WIFI = {wifi!r}\n" + script).encode()
    return bench.stream(run, bench.BOARD + ["run", "-", "--stream", "--timeout", "180"],
                        source, run.summary["plan"], wifi, original, vlc, False)


def assess(run, clean_board, ffmpeg):
    s = run.summary
    cap = s["capture"]
    if not s.get("rtsp_ready_at") or cap.get("operator_cancelled"):
        return "未验证"
    mp4 = run.directory / "recording.mp4"
    if mp4.is_file() and cap.get("frames"):
        cap["frame_numbers"] = frame_numbers(mp4, ffmpeg)
    gap = cap.get("frame_numbers", {}).get("gap_pct")
    passed = (clean_board and cap.get("transport_observed") == ["tcp"]
              and cap.get("continuous_display_s", 0) >= 60 and cap.get("video_track_seconds", 0) >= 58
              and (cap.get("width"), cap.get("height")) == (640, 360)
              and gap is not None and gap <= 1 and not cap.get("client_end_reason"))
    if passed and not s["simulation"] and not any(run.directory.glob("latency-*.png")):
        return "未验证"
    return "通过" if passed else "失败"


def wizard(args, ask=input):
    simulated = bool(args.simulate)
    vlc, sim_ffmpeg, mtx = bench.tools(*(args.simulate or ()))
    ffmpeg = sim_ffmpeg or shutil.which("ffmpeg") or bench.HERE / "runtime/ffmpeg.exe"
    original = None if simulated else wlan.snapshot()
    if not simulated and (not original or not original[2] or not Path(ffmpeg).is_file() or not bench.find_ffprobe()):
        raise RuntimeError("开始前需要可恢复的原 Wi-Fi 以及 FFmpeg、ffprobe。")
    if not simulated and original[1] == bench.config_wifi()["ssid"]:
        raise RuntimeError("请先将电脑连回日常 Wi-Fi，再打开向导。")
    if not simulated:
        if saved_sta():
            print("已找到第二轮用的随身 Wi-Fi 配置。")
        else:
            print("第二轮的随身 Wi-Fi 名称或密码还没填好。现在可以直接做第一轮；"
                  "只有需要第二轮时，按窗口提示输入名称和密码，会保存在本机。")
        print("第二轮需要随身 Wi-Fi 开启 2.4 GHz，并关闭“客户端隔离 / AP 隔离”，让电脑能连到板子。")
    root = args.runs / (bench.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-hubei")
    root.mkdir(parents=True)
    rows = []
    for mode in ("ap", "sta"):
        run = bench.Run(root / mode, plan(mode), simulated)
        outcome = "未验证"
        try:
            if simulated:
                cut = getattr(args, "simulate_cut", None) in (mode, "both")
                with bench.localhost_source(sim_ffmpeg, mtx, run.directory, ("tcp",), size="640x360",
                                            fps=60, gop=10, bitrate_kbps=1000,
                                            source_seconds=25 if cut else None) as url:
                    clean = bench.stream(run, bench.simulated_board(url, 35 if cut else 68), None,
                                         plan(mode), {}, None, vlc, True)
            else:
                clean = physical(run, mode, ask, original, vlc)
            outcome = assess(run, clean, ffmpeg)
        except (OSError, RuntimeError, ValueError, KeyError, EOFError, KeyboardInterrupt, subprocess.SubprocessError) as exc:
            run.summary["message"] = run.clean(str(exc)) if isinstance(exc, RuntimeError) else type(exc).__name__
            print(run.summary["message"])
            if isinstance(exc, RuntimeError) and run.summary.get("rtsp_ready_at") and "vlc" in run.summary["stages"]:
                if run.summary["capture"].get("client_end_reason"):
                    outcome = "失败"
        finally:
            restored = simulated
            if not simulated:
                try:
                    restored = wlan.restore(original, diagnostics=run.summary["network"])
                except (OSError, subprocess.TimeoutExpired):
                    restored = False
                run.summary["network"]["restored"] = restored
                if not restored:
                    print(wlan.MANUAL_RESTORE)
            images = sorted(p.name for p in run.directory.glob("latency-*.png"))
            run.summary["clock_images"] = images
            run.summary.update(result=outcome, passed=outcome == "通过")
            run.finish()
            rows.append(dict(mode=mode, result=outcome, capture=run.summary["capture"], clock_images=images,
                             restored=None if simulated else restored))
            result = conclusion([row["result"] for row in rows])
            bench.write_json(root / "summary.json", dict(simulation=simulated, rounds=rows, conclusion=result))
        if outcome != "失败" or not restored:
            break
    print(result, root)
    return int(rows[-1]["result"] != "通过" or not restored)
