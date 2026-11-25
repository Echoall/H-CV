"""Operator-led offline capture and H.264 comparison for M3 issue 6."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import time

from station.bench import bench, wlan
from station.bench.reset_capture import ResetCapture
from tools.analysis.m3_h264_compare import compare, picture_digests


def frame_numbers(path, ffmpeg):
    result = subprocess.run(
        [str(ffmpeg), "-nostdin", "-i", str(path), "-map", "0:v:0", "-c:v", "copy",
         "-bsf:v", "trace_headers", "-f", "null", "-"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=70, check=True,
    )
    fields = re.compile(r"\b(log2_max_frame_num_minus4|nal_unit_type|nal_ref_idc|first_mb_in_slice|frame_num)"
                        r"\s+[01]+\s*=\s*(\d+)")
    modulus = previous = kind = reference = first_mb = None
    seen = missing = idrs = 0
    for name, text in fields.findall(result.stderr):
        value = int(text)
        if name == "log2_max_frame_num_minus4":
            modulus = 1 << (4 + value)
        elif name == "nal_ref_idc":
            reference = value
        elif name == "nal_unit_type":
            kind = value
            first_mb = None
        elif name == "first_mb_in_slice":
            first_mb = value
        elif reference and first_mb == 0 and kind in (1, 5) and modulus:
            if kind == 5:
                idrs += 1
            elif previous is not None:
                missing += (value - previous - 1) % modulus
            if idrs:
                seen += 1
                previous = value
    return {"reference_pictures": seen, "idr": idrs, "skipped": missing,
            "gap_pct": 100 * missing / (seen + missing) if seen else None}


def decide(summary, board, pc, match):
    capture = summary["capture"]
    clip = summary.get("board_raw_clip", {})
    if not (clip.get("downloaded") and "sd_write_ms" in clip and capture.get("overlap_ready")
            and capture.get("latency_screenshots", 0) >= 5 and summary.get("recording_passed")
            and capture.get("video_track_seconds", 0) >= 60 and match.get("status") == "compared"
            and board.get("gap_pct") is not None and pc.get("gap_pct") is not None):
        return "证据不足"
    if board["skipped"]:
        return "板端原始短片已有缺口"
    if pc["gap_pct"] <= 1:
        return "通过"
    return "低码率复测" if match.get("missing_board_pictures", 0) else "证据不足"


def analyze_round(directory, data, ffmpeg):
    board = pc = match = {}
    raw = directory / "board-encoder.h264"
    mp4 = directory / "recording.mp4"
    result = dict(run=directory.name, recorded_seconds=data["capture"].get("video_track_seconds"),
                  camera_overlap_s=data["capture"].get("seconds_from_board_ready"),
                  screenshots=data["capture"].get("latency_screenshots"),
                  detector=data.get("detector_during_playback"), detector_full_session=data.get("detector_sample"),
                  raw_clip=data.get("board_raw_clip"), capture_message=data.get("message"))
    result["rtsp_attempts"] = data.get("rtsp_attempts", [])
    try:
        if data.get("board_raw_clip", {}).get("downloaded"):
            board = frame_numbers(raw, ffmpeg)
            if data["capture"].get("decodable") is not False:
                pc = frame_numbers(mp4, ffmpeg)
                match = compare(picture_digests(raw, ffmpeg), picture_digests(mp4, ffmpeg))
    except (OSError, subprocess.SubprocessError) as error:
        result["analysis_error"] = type(error).__name__
    result.update(board=board, pc=pc, comparison=match, verdict=decide(data, board, pc, match))
    return result


def uptime(run):
    code, out, err = run.command("uptime-" + str(time.monotonic_ns()), bench.BOARD + [
        "run", "-", "--timeout", "5"], timeout=15, data=b'import time\nprint("HCV_UP", time.ticks_ms())')
    match = re.search(r"(?m)^HCV_UP (\d+)", out)
    if code and "board.py backup command" in err:
        raise RuntimeError("此向导缺少本机原有的板卡备份清单。保持 USB 插着，联系我补齐向导目录。")
    if code or not match:
        raise RuntimeError("读不到板子运行时间。请检查 USB 数据线，关闭 CanMV，再重开本向导。")
    return int(match[1])


def field(args, ask=input):
    base = json.loads((bench.HERE / "plans/p7-ap-vicap30.json").read_text(encoding="utf-8"))
    base.update(id="field-direct", title="直连对照", latency=True, direct_rtsp=True,
                steps=["把镜头对准电脑屏幕左半边，保持固定。录像开始后左边会显示毫秒时钟。",
                       "保留左侧数字可见，可在旁边缓慢移动一支笔约十秒，用于检查运动时的色块。"])
    simulation = bool(args.simulate)
    stamp = bench.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    root = args.runs.resolve() / (stamp + "-field")
    session = bench.Run(root, base, simulation)
    rounds = []
    resets = []
    message = "证据不足"
    restored = False
    capture = None
    print("本向导在本机运行。无线收发已测完，本次先录一轮，必要时再录一轮。请留约 15 分钟。")
    try:
        bench.tools(*(args.simulate or ()))
        ffmpeg = args.simulate[0] if simulation else (shutil.which("ffmpeg") or bench.HERE / "runtime/ffmpeg.exe")
        if not Path(ffmpeg).is_file() or not bench.find_ffprobe():
            raise RuntimeError("缺少本地视频分析工具，请先补齐 FFmpeg 和 ffprobe。")
        if not simulation:
            wifi = bench.config_wifi()
            args.original = wlan.snapshot()
            if not args.original or not args.original[2] or args.original[1] == wifi["ssid"]:
                raise RuntimeError("请先连接日常 Wi-Fi；若仍读不到，在 Windows 设置的「隐私和安全性 → 位置」打开位置服务。")
            print("Windows 无线详情可读。" if wlan.netsh("show", "interfaces").returncode == 0 else
                  "Windows 未开放位置详情，已通过已连接网络验证恢复配置。")
            print("已核对原 Wi-Fi 和恢复配置。正在检查 USB。")
            if session.command("initial-inventory", bench.BOARD + ["info"], timeout=15)[0]:
                raise RuntimeError("USB 自检失败。确认已连接 K230 并关闭 CanMV。")
            capture = args.reset_capture = ResetCapture(root)
            print("Windows 将询问是否允许记录连接重置。任务栏可能有闪烁的盾牌图标。"
                  "可在 90 秒内选择，选「否」也能继续录像。", flush=True)
            if capture.prepare() != "ready":
                print("本轮跳过网络抓包，继续现场测试。", flush=True)
        args.runs = root
        for index in (1, 2):
            if not simulation:
                before = uptime(session)
                started = time.monotonic()
                reply = ask("拔掉板子全部电源，等灯全灭 10 秒，插回 USB。完成后按回车，退出输入 q > ")
                if reply.strip().lower() == "q":
                    message = "用户结束"
                    break
                if session.command(f"inventory-after-power-{index}", bench.BOARD + ["info"], timeout=18)[0]:
                    raise RuntimeError("重新插入后 USB 自检失败，本轮已停止。")
                after = uptime(session)
                elapsed = round(1000 * (time.monotonic() - started))
                verified = after < before + elapsed - 7000 and after <= elapsed + 2000
                resets.append(dict(before_ms=before, after_ms=after, elapsed_ms=elapsed, verified=verified))
                if not verified:
                    raise RuntimeError("运行时间无法证实这次断电，记录已保存。")
            plan = base if index == 1 else dict(base, id="field-low", title="低码率直连复测",
                                                width=640, height=360, fps=20, gop=20, bitrate_kbps=800)
            bench.main(args, ask, selected_plan=plan)
            results = sorted(root.glob("*-" + plan["id"] + "/summary.json"))
            if not results:
                break
            directory = results[-1].parent
            data = json.loads(results[-1].read_text(encoding="utf-8"))
            row = analyze_round(directory, data, ffmpeg)
            message = row["verdict"]
            rounds.append(row)
            print(f"本轮机器判读：{message}。")
            if message != "低码率复测" or not data["network"].get("restored"):
                break
            if index == 2:
                message = "低码率复测后仍有缺口"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, EOFError, KeyboardInterrupt) as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else "测试或判读中止，请保留现场记录。"
    finally:
        if not simulation and getattr(args, "original", None):
            try:
                restored = wlan.snapshot() == args.original
                if not restored:
                    restored = wlan.restore(args.original)
            except (OSError, subprocess.TimeoutExpired):
                restored = False
        report = dict(simulation=simulation, result=message, restored=restored, threshold_pct=1,
                      udp_evidence="20261125-234752-178860-p8-link", power_cycles=resets, rounds=rounds)
        if capture:
            report["reset_capture"] = capture.finish()
        bench.write_json(root / "summary.json", report)
        text = (f"# 离线现场记录\n\n{message}。原 Wi-Fi 恢复核验：{restored}。"
                "\n\n缺口占比为跳过的帧号数除以跳过数与收到的参考帧数之和。分支阈值为 1%。"
                "\n\n```json\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n")
        (root / "summary.md").write_text(text, encoding="utf-8")
        print(message, f"\n记录目录 {root}")
        print("已核实恢复原 Wi-Fi，可以回去找 GPT 了。" if restored else
              ("模拟结束。" if simulation else "请核对电脑已连回原 Wi-Fi，再回去找 GPT。"))
    return int(message != "通过")
