"""Offline M3 test wizard. Run with python -m station.bench.bench."""

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime
from fractions import Fraction
from hashlib import sha256
from importlib import import_module
from ipaddress import ip_address, IPv4Address
import json
from pathlib import Path
from queue import Empty, Queue
import re
import shutil
import socket
import subprocess
import sys
from threading import Event, Thread
import time
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape

from station.bench import latency, rtp_trace, wlan
from station.record_rtsp import find_vlc
from tools.analysis.m3_sta_record import decode, redact as redact_rtsp
from tools.analysis.m3_vlc_smoke import localhost_source, record_with_libvlc


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BOARD = [sys.executable, str(ROOT / "tools/board/board.py")]
MODULES = ("config.py", "guard.py", "media_session_guard.py", "stream.py",
           "ch0_configurable_probe.py", "tcp_control.py")
LIMITS = {
    "fps": (15, 30), "bitrate_kbps": (500, 2000), "gop": (10, 60),
    "caching_ms": (100, 1500), "seconds": (5, 60),
}
STEPS = (
    "接上板子。指示灯亮后，关闭可能正在使用板子的 CanMV 软件。",
    "让镜头朝向要看的地方。进入下一步后，电脑会临时连到板子的 Wi-Fi。",
    "画面窗口打开后观察是否卡顿。录制和板端清理结束后会回到原来的网络。",
)


def now():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_plan(plan):
    fields = {"id", "version", "title", "steps", "width", "height", "transport", "criteria", *LIMITS}
    optional = {"latency", "sensor_mode", "firmware_encoder", "vicap_stream_limit", "direct_rtsp"}
    if type(plan) is not dict or (set(plan) - optional) != fields:
        raise ValueError("方案字段不齐或多出了字段")
    if not isinstance(plan["id"], str) or not re.fullmatch(r"[a-z0-9-]{1,32}", plan["id"]):
        raise ValueError("方案编号无效")
    if type(plan["version"]) is not int or plan["version"] < 1:
        raise ValueError("版本号必须是正整数")
    if not isinstance(plan["title"], str) or not plan["title"].strip():
        raise ValueError("方案缺少名称")
    steps = plan["steps"]
    if not isinstance(steps, list) or not steps or any(not isinstance(s, str) or not s.strip()
                                                       for s in steps):
        raise ValueError("方案缺少操作步骤")
    if any(type(plan[field]) is not int for field in ("width", "height")) or (
        plan["width"], plan["height"]
    ) not in ((960, 540), (640, 360)):
        raise ValueError("画面大小只支持 960×540 或 640×360")
    for key, (low, high) in LIMITS.items():
        if type(plan[key]) is not int or not low <= plan[key] <= high:
            raise ValueError(f"方案数值超出范围 {key}（{low}–{high}）")
    if "latency" in plan and (plan["latency"] is not True or plan["seconds"] <= latency.SAMPLE_SECONDS[-1]):
        raise ValueError("延迟方案需要留足截图时间")
    if "sensor_mode" in plan:
        mode = plan["sensor_mode"]
        if (type(mode) is not list or any(type(value) is not int for value in mode)
                or mode not in ([1920, 1080, 30], [1280, 720, 90])):
            raise ValueError("相机原生模式不在调试台支持的范围内")
    if "firmware_encoder" in plan and (
        plan["firmware_encoder"] is not True or plan.get("sensor_mode") != [1280, 720, 90]
        or (plan["fps"] != 30 and not plan.get("direct_rtsp"))
    ):
        raise ValueError("编码降帧方案要求相机每秒 90 帧、输出每秒 30 帧")
    if plan["transport"] not in ("tcp", "udp"):
        raise ValueError("传输方式只支持 TCP 或 UDP")
    if "vicap_stream_limit" in plan and (
        plan["vicap_stream_limit"] is not True or not plan.get("firmware_encoder")
        or plan["transport"] != "tcp" or plan["seconds"] != 60
    ):
        raise ValueError("VICAP 诊断需要 60 秒 TCP 录像及固件编码器")
    if "direct_rtsp" in plan and (
        plan["direct_rtsp"] is not True or not plan.get("vicap_stream_limit") or not plan.get("latency")
    ):
        raise ValueError("直连对照需要 VICAP 限帧和延迟截图")
    criteria = plan["criteria"]
    if type(criteria) is not dict or set(criteria) != {"min_duration_ratio", "min_frame_ratio"}:
        raise ValueError("方案缺少判断标准")
    if any(type(v) not in (int, float) or not 0.8 <= v <= 1 for v in criteria.values()):
        raise ValueError("通过门槛必须在 80% 到 100% 之间")
    return plan


def free_plan(base, ask=input):
    plan = {**base, "id": "free", "title": "自由调试", "steps": list(STEPS)}
    print("直接按回车沿用括号里的默认值。")
    labels = {"fps": "每秒画面数", "bitrate_kbps": "每秒视频数据量（千比特）",
              "gop": "每隔多少帧发一张完整图", "caching_ms": "播放器缓冲（毫秒）",
              "seconds": "录制秒数"}
    while True:
        try:
            size = ask("画面大小，1 为 960×540，2 为 640×360（1）> ").strip() or "1"
            plan["width"], plan["height"] = {"1": (960, 540), "2": (640, 360)}[size]
            method = ask("接收方式，1 为可靠传输，2 为低等待传输（1）> ").strip() or "1"
            plan["transport"] = {"1": "tcp", "2": "udp"}[method]
            for field, (low, high) in LIMITS.items():
                value = ask(f"{labels[field]}，{low}–{high}（{base[field]}）> ").strip()
                plan[field] = int(value) if value else base[field]
            return validate_plan(plan)
        except (KeyError, ValueError):
            print("有一项超出了显示的范围，请重新填写。")


def redact(text, private):
    escaped = set()
    for value in filter(None, private):
        escaped.update((value, quote(value, safe=""), escape(value),
                        json.dumps(value, ensure_ascii=True)[1:-1],
                        json.dumps(value, ensure_ascii=False)[1:-1]))
    for value in sorted(escaped, key=len, reverse=True):
        text = re.sub(re.escape(value), "<private>", text, flags=re.IGNORECASE)
    text = redact_rtsp(text, {"ssid": "", "password": ""})
    text = re.sub(r"(?i)(?<![\da-f])(?:[\da-f]{2}[:-]){5}[\da-f]{2}(?![\da-f])", "<mac>", text)

    def ipv6(match):
        try:
            if ip_address(match[0].split("%", 1)[0]).version == 6:
                return "<ipv6>"
        except ValueError:
            pass
        return match[0]

    return re.sub(r"(?i)(?<![\da-f:])[\da-f:]*:[\da-f:]+(?:%[\w.-]+)?", ipv6, text)


class Run:
    def __init__(self, directory, plan, simulation):
        self.directory = directory
        directory.mkdir(parents=True)
        self.private = set()
        self.summary = {"plan": plan, "simulation": simulation, "started": now(),
                        "stages": {}, "network": {}, "capture": {}, "observations": {}}

    def clean(self, text):
        return redact(text, self.private)

    def line(self, name, text):
        with (self.directory / name).open("a", encoding="utf-8") as output:
            output.write(self.clean(text.rstrip("\r\n")) + "\n")

    def command(self, name, arguments, *, timeout, data=None):
        start = now()
        try:
            result = subprocess.run(arguments, cwd=ROOT, capture_output=True, input=data,
                                    timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            out = (error.output or b"", error.stderr or b"")
            code = None
        else:
            out = (result.stdout, result.stderr)
            code = result.returncode
        self.summary["stages"][name] = {"started": start, "ended": now(), "exit_code": code}
        stdout, stderr = (content.decode("utf-8", errors="replace") for content in out)
        for channel, text in (("stdout", stdout), ("stderr", stderr)):
            self.line(f"{name}.{channel}.log", text)
        return code, stdout, stderr

    def finish(self):
        def clean_values(value):
            if isinstance(value, dict):
                return {key: clean_values(item) for key, item in value.items()}
            if isinstance(value, list):
                return [clean_values(item) for item in value]
            return self.clean(value) if isinstance(value, str) else value

        self.summary["ended"] = now()
        safe = clean_values(self.summary)
        write_json(self.directory / "summary.json", safe)
        if safe["plan"]["id"] == "m1-v18":
            mode = "历史日志回放" if safe["simulation"] else "板上实测"
            lines = [f"# {safe['plan']['title']}（{mode}）", "", safe["message"]]
            if "measurement" in safe:
                m = safe["measurement"]
                drops = m["wall_duration_dropped"]
                lines.extend(["", f"- 处理帧数 {m['summary']['frames']}",
                              f"- 最大窗口处理 p95 为 {m['max_sample_process_p95_us']} 微秒",
                              f"- 完整采集时长法估计丢帧 {drops if drops is not None else '缺少起止记录'}",
                              f"- 活动与回合间 GC 合计估计丢帧 {m.get('duration_dropped_including_gc', '缺少回合记录')}"])
            (self.directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
        if safe["plan"]["id"] == "p8-link":
            lines = [f"# {safe['plan']['title']}", "", safe["message"] if "message" in safe else "测试进行中", ""]
            for row in safe.get("trials", []):
                direction = "板子 → 电脑" if row["direction"] == "board_to_pc" else "电脑 → 板子"
                measured = row.get("receive_kBps")
                outcome = "待测" if "sustained_target" not in row else (
                    "完成" if row["sustained_target"] else "未达到该档")
                label = f"{row['protocol'].upper()} {direction} {row['target_kBps']} kB/s，{row['seconds']} 秒"
                if row.get("shape"):
                    label = f"{row['shape']} 档 {label}"
                lines.append(f"- {label}。{outcome}" + (f"，实收 {measured} kB/s。" if measured is not None else "。"))
            if "restored" in safe["network"]:
                status = "是" if safe["network"]["restored"] else "否"
                lines.append(f"- 原来的网络已恢复：{status}")
            (self.directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
        capture = safe["capture"]
        state = "达到方案门槛" if safe.get("passed") else "未达到方案门槛"
        if safe["plan"]["id"] == "preflight":
            state = "自检通过，未运行方案" if safe.get("preflight_passed") else "自检未通过"
        delayed = safe.get("latency")
        delay_line = ""
        if delayed:
            count = delayed["sample_count"]
            if count < latency.MIN_SAMPLES:
                delay_line = f"- 延迟待读，已有 {count} 组可用读数。\n"
                if safe.get("recording_passed"):
                    state = "录像达到门槛，延迟待读"
            else:
                delay_line = (f"- 延迟取 {count} 组，中位数 {delayed['median_ms']} ms，"
                              f"最大值 {delayed['max_ms']} ms。\n")
        sample = "电脑模拟" if safe["simulation"] else "板子实拍"
        observations = "\n".join(f"- {name}：{value}" for name, value in safe["observations"].items())
        if "motion_quality" in safe and safe["motion_quality"] is None and safe.get("recording_passed"):
            state = "录像达到门槛，画质待判"
        elif safe.get("motion_quality") is not None:
            observations = f"- 移动画质：{safe['motion_quality']}/5\n{observations}"
        restored = {True: "是", False: "否"}.get(safe["network"].get("restored"), "无需切换")
        if safe["network"].get("manual") and "restored" not in safe["network"]:
            restored = "请手动切回"
        network_reason = {
            "windows_rejected_network_command": "Windows 拒绝了切回原网络的指令。",
            "original_network_not_confirmed_in_time": "发出了切回指令，但没能在时限内确认原网络。",
            "windows_command_timeout": "Windows 检查或切网指令超时。",
            "windows_command_unavailable": "无法调用 Windows 的切网功能。",
        }.get(safe["network"].get("restore_reason"), "")
        if network_reason:
            network_reason = f"- {network_reason}\n"
        (self.directory / "summary.md").write_text(
            f"# {safe['plan']['title']}（{sample}）\n\n"
            f"- 结果：{state}\n- 录到 {capture.get('frames', 0)} 帧，"
            f"共 {capture.get('video_track_seconds', capture.get('seconds', 0)):.2f} 秒。\n"
            f"- 原来的网络已恢复：{restored}\n"
            f"{network_reason}"
            f"{delay_line}"
            f"- 说明：{safe.get('message', '录制与清理已结束。')}\n{observations}\n",
            encoding="utf-8",
        )


def config_wifi():
    path = HERE / "config.local.json"
    try:
        if not path.exists():
            prior = ROOT / "k230/ap/config.local.json"
            if not prior.is_file():
                raise RuntimeError("缺少板子网络的本地配置，请联系我补齐后再测。")
            write_json(path, {"wifi": json.loads(prior.read_text(encoding="utf-8"))["wifi"]})
        wifi = json.loads(path.read_text(encoding="utf-8"))["wifi"]
    except (OSError, KeyError, ValueError, TypeError):
        raise RuntimeError("板子网络的本地配置有误，请联系我检查。") from None
    if (type(wifi) is not dict or wifi.get("mode") != "ap" or not isinstance(wifi.get("ssid"), str)
            or not 1 <= len(wifi["ssid"].encode("utf-8")) <= 32
            or not isinstance(wifi.get("password"), str) or not 8 <= len(wifi["password"]) <= 63
            or not wifi["password"].isascii() or not wifi["password"].isprintable()
            or any(not c.isprintable() for c in wifi["ssid"])
            or type(wifi.get("channel")) is not int or not 1 <= wifi["channel"] <= 13):
        raise RuntimeError("板子网络的本地配置有误，请联系我检查。")
    return {key: wifi[key] for key in ("mode", "ssid", "password", "channel")}


def preflight(run):
    source = (
        'import sys, os, time, hashlib, binascii, json\nsys.path.insert(0, "/sdcard/hcv")\n'
        'from hcv.media_session_guard import _marker_path, _read_marker, _ticks_diff\n'
        'path = _marker_path()\n'
        'try:\n    mark = _read_marker(path)\n'
        'except OSError as e:\n'
        '    if e.args[0] != 2: raise\n    mark = None\n'
        'print("HCV_BENCH_USED", int(mark is not None and _ticks_diff(time.ticks_ms(), mark) >= 0))\n'
        'print("HCV_BENCH_FALLBACK", int(path.startswith("/sdcard/")))\n'
        'paths = {}\n'
        f'for name in {MODULES!r}:\n'
        '    try:\n'
        '        with open("/sdcard/hcv/hcv/" + name, "rb") as f:\n'
        '            paths[name] = binascii.hexlify(hashlib.sha256(f.read()).digest()).decode()\n'
        '    except OSError:\n        paths[name] = ""\n'
        'print("HCV_BENCH_HASHES " + json.dumps(paths))\n'
    )
    code, text, _ = run.command("board-inventory", BOARD + ["run", "-", "--timeout", "5"],
                                timeout=18, data=source.encode("utf-8"))
    if code != 0:
        raise RuntimeError("没有读到板子状态。确认数据线插好、CanMV 已关闭，再打开调试台。")
    if "HCV_BENCH_USED 1" in text:
        message = "板子这次上电已经用过相机。请完全拔掉电源，等指示灯全灭 10 秒，再插回后重开调试台。"
        if "HCV_BENCH_FALLBACK 1" in text:
            message += "若完全断电后仍遇到本提示，请联系我检查板子的运行标志。"
        raise RuntimeError(message)
    hash_line = next((line for line in text.splitlines()
                      if line.startswith("HCV_BENCH_HASHES ")), None)
    if "HCV_BENCH_USED 0" not in text or hash_line is None:
        raise RuntimeError("未能确认板子的相机状态。请关闭调试台并把自检记录留给我。")
    code, _, _ = run.command("board-info", BOARD + ["info"], timeout=15)
    if code != 0:
        raise RuntimeError("读取板子信息失败。请关闭 CanMV，确认数据线插好后再打开调试台。")
    return json.loads(hash_line.split(" ", 1)[1])


def prepare_board_files(run, previous):
    for name in MODULES:
        source = ROOT / "k230/hcv" / name
        if previous.get(name) != sha256(source.read_bytes()).hexdigest():
            code, _, _ = run.command("board-sync-" + source.stem, BOARD + [
                "put", str(source), "/sdcard/hcv/hcv/" + name,
            ], timeout=25)
            if code != 0:
                raise RuntimeError("准备板子程序失败。日志已保存，请关闭调试台并联系我。")


def board_entry(plan, wifi, raw_clip_path=None):
    override = {
        "wifi": wifi,
        "chn_stream": {"width": plan["width"], "height": plan["height"]},
        "stream": {"enabled": True, "port": 8554, "path": "hcv",
                   **{key: plan[key] for key in ("fps", "bitrate_kbps", "gop")}},
    }
    if "sensor_mode" in plan:
        override["sensor"] = dict(zip(("width", "height", "fps"), plan["sensor_mode"]))
    source = ROOT / "tools/board/m3_ch0_ap_configurable_probe.py"
    return (f"HCV_STREAM_SECONDS = {plan['seconds'] + 65}\nHCV_CONFIG_OVERRIDE = {override!r}\n"
            f"HCV_USE_FIRMWARE_ENCODER = {plan.get('firmware_encoder', False)!r}\n"
            f"HCV_VICAP_STREAM_LIMIT = {plan.get('vicap_stream_limit', False)!r}\n"
            f"HCV_DIRECT_RTSP = {plan.get('direct_rtsp', False)!r}\n"
            f"HCV_RAW_CLIP_PATH = {raw_clip_path!r}\n"
            + source.read_text(encoding="utf-8")).encode("utf-8")


def simulated_board(url, seconds):
    from urllib.parse import urlsplit

    endpoint = urlsplit(url)
    private = f"HCV_RTSP_PRIVATE,{endpoint.hostname},{endpoint.port},{endpoint.path[1:]}"
    return [
        sys.executable, "-u", "-c",
        'import sys,time; print("HCV_MEDIA_GUARD,claimed"); print("HCV_AP_READY"); '
        'print(sys.argv[1]); print("HCV_CH0_CONFIG_READY"); time.sleep(float(sys.argv[2])); '
        'print("HCV_CH0_CONFIG_CLEANUP")', private, str(seconds + 4),
    ]


def tools(local_ffmpeg=None, local_mediamtx=None):
    if sys.version_info < (3, 10):
        raise RuntimeError("电脑上的 Python 版本太旧。请联系我准备 Python 3.10 或更新版本。")
    dependencies = {
        "cv2": "录像读取", "serial": "USB 连接", "mpremote": "板子通信",
        "tkinter": "显示窗口", "PIL.ImageGrab": "屏幕截图",
    }
    for module, task in dependencies.items():
        try:
            import_module(module)
        except (ImportError, OSError) as error:
            raise RuntimeError(f"电脑缺少{task}组件。请联系我补齐项目依赖，再重新打开调试台。") from error
    override = HERE / "config.local.json"
    try:
        settings = json.loads(override.read_text(encoding="utf-8")) if override.is_file() else {}
        if type(settings) is not dict:
            raise ValueError("local config must be a JSON object")
    except (OSError, ValueError):
        raise RuntimeError("调试台的本地配置读不开。请联系我检查板子的网络设置。") from None
    vlc = settings.get("vlc") or HERE / "runtime/vlc/vlc.exe"
    if not Path(vlc).is_file():
        try:
            vlc = find_vlc()
        except FileNotFoundError:
            raise RuntimeError("这台电脑找不到播放器。请联系我补齐调试台的播放器。") from None
    if not all(Path(vlc).with_name(name).is_file() for name in ("libvlc.dll", "libvlccore.dll")):
        raise RuntimeError("播放器的文件不齐。请联系我补齐 VLC 后重新打开。")
    if local_ffmpeg is not None and not all(p.is_file() for p in (local_ffmpeg, local_mediamtx)):
        raise RuntimeError("没有找到回环模拟用的 FFmpeg 或 MediaMTX 文件。")
    return str(Path(vlc).resolve()), local_ffmpeg, local_mediamtx


def find_ffprobe():
    portable = HERE / "runtime/ffprobe.exe"
    return str(portable) if portable.is_file() else shutil.which("ffprobe")


def inspect_frame_rate(run, recording, ffprobe):
    fields = "stream=r_frame_rate,duration"
    code, stdout, _ = run.command("ffprobe-recording", [
        ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
        fields, "-of", "default=noprint_wrappers=1", str(recording),
    ], timeout=8)
    if code == 0:
        for line in stdout.splitlines():
            name, _, value = line.partition("=")
            if name == "r_frame_rate" and re.fullmatch(r"[1-9]\d*/[1-9]\d*", value):
                run.summary["capture"][name] = value
            if name == "duration" and re.fullmatch(r"\d+(?:\.\d+)?", value):
                run.summary["capture"]["video_track_seconds"] = float(value)


def motion_snapshot(recording, output, seconds):
    import cv2

    source = cv2.VideoCapture(str(recording))
    try:
        source.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, frame = source.read()
        return bool(ok and cv2.imwrite(str(output), frame))
    finally:
        source.release()


def receive(run, executable, endpoint, plan, simulated):
    path = run.directory / "recording.mp4"
    seconds = plan["seconds"] + (2 if plan.get("direct_rtsp") else 0)
    show = "0" if simulated and plan.get("direct_rtsp") else (
        "2" if plan.get("latency") else ("0" if simulated else "1"))
    command = [
        sys.executable, "-m", "station.bench.bench", "--player",
        executable, endpoint, str(path), str(seconds), plan["transport"],
        str(plan["caching_ms"]), show, run.summary.get("rtsp_ready_at", ""),
    ]
    if plan.get("hubei"):
        command.append("hubei")
    if plan.get("receiver_ab") == "vlc200":
        code, stdout, stderr = run.command("vlc", command, timeout=seconds + 20)
    elif plan.get("direct_rtsp"):
        code, stdout, stderr = direct_playback(run, command, seconds, urlsplit(endpoint).hostname)
    else:
        code, stdout, stderr = run.command("vlc", command, timeout=seconds + 16)
    media = run.summary["capture"]
    if plan.get("vicap_stream_limit"):
        media["vlc_error_lines"] = [
            run.clean(line.strip()) for line in stderr.splitlines()
            if re.search(r"\b(error:|(?:connection|read) timeout|no data received|RuntimeError:)", line, re.I)
        ]
    transports = set()
    for match in re.finditer(r"(?im)^Transport:\s*RTP/AVP([^;\r\n]*)", stderr):
        transports.add("tcp" if match[1].upper() == "/TCP" else "udp")
    media["transport_observed"] = sorted(transports)
    for line in stdout.splitlines():
        if line.startswith(("HCV_CLIENT_ENDED ", "HCV_CLIENT_PLAYING ", "HCV_CLIENT_DONE ", "HCV_DISPLAY ")):
            event = json.loads(line.partition(" ")[2])
            media.update(event)
    media["exit_code"] = code
    if path.is_file() and path.stat().st_size > 0:
        try:
            media.update(decode(path, run.directory / "preview.png"))
        except RuntimeError:
            media["decodable"] = False
    if plan.get("latency"):
        media["latency_screenshots"] = len(list(run.directory.glob("latency-*.png")))
    if (plan.get("firmware_encoder") or plan.get("hubei")) and media.get("frames"):
        inspect_frame_rate(run, path, find_ffprobe())
        if not plan.get("latency"):
            sample_at = min(plan["seconds"] / 2, media["seconds"] / 2)
            media["motion_snapshot"] = motion_snapshot(path, run.directory / "motion-preview.png", sample_at)
    return media


def direct_playback(run, command, seconds, host):
    attempts = run.summary["rtsp_attempts"] = []
    ready = datetime.fromisoformat(run.summary["rtsp_ready_at"]).timestamp()
    # The board sends for plan.seconds + 65 s from the ready marker.
    remaining = ready + run.summary["plan"]["seconds"] + 65 - time.time()
    deadline = time.monotonic() + remaining
    outcome = (None, "", "board session ended before playback")
    for index in range(1, 6):
        remaining = deadline - time.monotonic()
        if remaining < seconds:
            break
        stage = f"vlc-attempt-{index}"
        ping_result = {}

        def ping():
            try:
                reply = subprocess.run(["ping", "-n", "1", "-w", "1000", host],
                                       capture_output=True, timeout=3)
                ping_result.update(exit_code=reply.returncode,
                                   echo_reply=bool(re.search(rb"(?i)\bTTL\s*=", reply.stdout)))
            except (OSError, subprocess.TimeoutExpired):
                ping_result["echo_reply"] = None

        probe = Thread(target=ping, daemon=True)
        probe.start()
        attempt_command = list(command)
        path_index = attempt_command.index("--player") + 3
        recording = Path(attempt_command[path_index])
        attempt_recording = recording.with_name(f"attempt-{index}.mp4")
        attempt_command[path_index] = str(attempt_recording)
        outcome = run.command(stage, attempt_command, timeout=min(seconds + 20, remaining))
        probe.join(4)
        code, stdout, stderr = outcome
        requests = re.findall(r"(?im)^Sending request: (OPTIONS|DESCRIBE|SETUP|PLAY)\b", stderr)
        responses = [int(x) for x in re.findall(r"(?im)^RTSP/1\.0\s+(\d{3})", stderr)]
        playing = "HCV_CLIENT_PLAYING " in stdout
        play_reply = re.search(r"(?is)Received a complete PLAY response:\s+RTSP/1\.0 2\d\d", stderr)
        errors = [run.clean(line.strip()) for line in stderr.splitlines()
                  if re.search(r"(?:demux error:|recvfrom\(\) error:|RuntimeError:)", line, re.I)]
        attempts.append(dict(index=index, **run.summary["stages"][stage], playing=playing,
                             rtsp_status_codes=responses,
                             rtsp_stage=requests[-1] if requests else "connect", ping=ping_result,
                             errors=errors, socket_error_codes=re.findall(
                                 r"\b(?:100\d{2}|ECONNRESET|ETIMEDOUT|ECONNREFUSED)\b", stderr)))
        run.summary["stages"]["vlc"] = run.summary["stages"][stage].copy()
        cancelled = "Esc" in stderr and "RuntimeError" in stderr
        if cancelled:
            run.summary["capture"]["operator_cancelled"] = True
        if playing or play_reply or cancelled or code == 0 or index == 5 or deadline - time.monotonic() < seconds + 2:
            if attempt_recording.exists():
                attempt_recording.replace(recording)
            break
        print(f"第 {index} 次开播连接失败，2 秒后重新连接。", flush=True)
        time.sleep(2)
    return outcome


def connect_board(run, original, wifi):
    try:
        auto = wlan.connect(original, wifi["ssid"], wifi["password"])
    except (OSError, subprocess.TimeoutExpired):
        auto = False
    run.summary["network"]["automatic_switch"] = auto
    if auto:
        return True
    return False


def wait_for_board(run, host, port, seconds):
    started = time.monotonic()
    while True:
        try:
            with socket.create_connection((host, port), timeout=1):
                port_open = True
        except OSError:
            port_open = False
        if port_open or time.monotonic() - started >= seconds:
            break
        time.sleep(0.5)
    reachable = port_open
    if not port_open:
        ping = ["ping", "-n", "1", "-w", "800"] if sys.platform == "win32" else [
            "ping", "-c", "1", "-W", "1",
        ]
        try:
            code, output, _ = run.command("board-ping", ping + [host], timeout=2)
            reachable = code == 0 and "ttl=" in output.lower()
        except OSError:
            reachable = None
    run.summary["network"].update(board_reachable=reachable, rtsp_port_open=port_open,
                                  reachability_checked_at=now())
    return port_open


def show_board_network(wifi):
    print(wlan.MANUAL_CONNECT)
    print(f"网络名称 {wifi['ssid']}　密码 {wifi['password']}")
    print("请在 20 秒内连好。电脑可能显示无互联网，这是正常的。", flush=True)


def receive_control(run, host, port, connect_seconds, board_closed=None):
    expected = 190_000 * 60
    banner = b"HCVTCP1\n"
    received = bad_bytes = 0
    retry_until = time.monotonic() + connect_seconds
    results = run.summary["tcp_control"] = {"expected_bytes": expected, "complete": False}
    while True:
        if board_closed is not None and board_closed.is_set():
            results["error"] = "board_control_closed"
            return
        try:
            conn = socket.create_connection((host, port), timeout=1)
            break
        except OSError:
            if time.monotonic() >= retry_until:
                results["error"] = "connect"
                return
            time.sleep(0.25)
    started = time.monotonic()
    results["started"] = now()
    try:
        with conn:
            conn.settimeout(4)
            greeting = bytearray()
            while len(greeting) < len(banner):
                data = conn.recv(len(banner) - len(greeting))
                if not data:
                    break
                greeting.extend(data)
            if greeting != banner:
                results["error"] = "control_banner_missing"
                return
            while received < expected and time.monotonic() - started < 68:
                data = conn.recv(min(65536, expected - received))
                if not data:
                    break
                received += len(data)
                bad_bytes += len(data) - data.count(b"K")
            if received == expected:
                conn.shutdown(socket.SHUT_WR)
                results["complete"] = not conn.recv(1) and bad_bytes == 0
    except OSError as error:
        results["error"] = type(error).__name__
    finally:
        results.update(ended=now(), bytes=received, bad_bytes=bad_bytes,
                       seconds=round(time.monotonic() - started, 3))


def capture_detector_rate(run, samples):
    arrival = run.summary.get("rtp_arrival", {})
    key = "detector_during_rtp"
    if run.summary["plan"].get("direct_rtsp"):
        capture = run.summary["capture"]
        started = capture.get("client_playing_at")
        ended = capture.get("client_end_at") or capture.get("client_done_at")
        if not started or not ended:
            return
        arrival = {"first_at_ms": round(datetime.fromisoformat(started).timestamp() * 1000),
                   "last_at_ms": round(datetime.fromisoformat(ended).timestamp() * 1000)}
        key = "detector_during_playback"
    if "first_at_ms" not in arrival:
        return
    overlap = [sample for sample in samples
               if arrival["first_at_ms"] <= sample[0] <= arrival["last_at_ms"]]
    if len(overlap) < 2:
        return
    _, start_ms, start_frames, start_errors = overlap[0]
    _, end_ms, end_frames, end_errors = overlap[-1]
    elapsed = end_ms - start_ms
    if elapsed > 0:
        frames = end_frames - start_frames
        run.summary[key] = {
            "channel": 1, "elapsed_ms": elapsed, "frames": frames,
            "errors": end_errors - start_errors, "fps": frames * 1000 / elapsed,
        }


def stream(run, command, source, plan, wifi, original, vlc, simulation):
    staging = run.summary["stages"]["board-session"] = {"started": now()}
    events = Queue()
    control_closed = Event()
    state = {"cleanup": False}
    detector_samples = []
    vicap = plan.get("vicap_stream_limit")
    direct = plan.get("direct_rtsp")

    def collect(pipe):
        for line in pipe:
            clean = line.strip()
            received_at = now()
            run.line("board.serial.log", f"{received_at} {line}")
            if clean == "HCV_TCP_CONTROL_CLOSED":
                control_closed.set()
            if clean.startswith(("HCV_RTSP_PRIVATE,", "HCV_CH0_CONFIG_READY", "HCV_AP_READY",
                                 "HCV_MEDIA_SESSION_", "HCV_CH0_CONFIG_CLEANUP",
                                 "HCV_TCP_CONTROL_READY", "HCV_VICAP_DETECT,", "HCV_RTSP_FIRST_IDR,",
                                 "HCV_RAW_CLIP_", "HCV_CLIP_RESUMED,", "HCV_HUBEI_STATUS,")):
                events.put((clean, received_at))
        events.put(None)

    with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          stdin=subprocess.PIPE, encoding="utf-8", errors="replace") as process:
        worker = Thread(target=collect, args=(process.stdout,), daemon=True)
        worker.start()
        try:
            if source:
                process.stdin.write(source.decode("utf-8"))
            process.stdin.close()
            endpoint = None
            use_manual = False
            deadline = time.monotonic() + plan["seconds"] + 90
            if plan.get("hubei") and not simulation:
                deadline += 45  # Allow bounded STA association before the 125-second stream.
            if vicap and not direct and not simulation:
                deadline += 120
            while True:
                remaining = deadline - time.monotonic()
                try:
                    item = events.get(timeout=max(0, remaining))
                except Empty:
                    raise RuntimeError("板子在规定时间内没有完成，请保存日志后联系我。") from None
                if item is None:
                    break
                event, received_at = item
                if event.startswith("HCV_HUBEI_STATUS,"):
                    values = dict(field.split("=") for field in event.split(",")[1:])
                    run.summary.setdefault("board_heartbeats", []).append(
                        dict(at=received_at, elapsed_ms=int(values["elapsed_ms"]), frames=int(values["frames"]),
                             bytes=int(values["bytes"]), fps=float(values["fps"])))
                elif event == "HCV_AP_READY":
                    capture = getattr(run, "reset_capture", None)
                    if capture is not None and endpoint:
                        capture.start(urlsplit(endpoint).hostname)
                    run.summary["network"]["switch_started"] = now()
                    use_manual = not simulation and (
                        original is None or not connect_board(run, original, wifi)
                    )
                    run.summary["network"]["manual"] = use_manual
                    if use_manual:
                        if plan.get("hubei"):
                            raise RuntimeError("电脑未能确认连接板载 AP，本轮网络对照无效。")
                        show_board_network(wifi)
                elif event.startswith("HCV_RTSP_PRIVATE,"):
                    _, address, port, path = event.split(",", 3)
                    endpoint = f"rtsp://{IPv4Address(address)}:{int(port)}/{path}"
                elif event == "HCV_TCP_CONTROL_READY" and endpoint:
                    print("正在做 60 秒纯网络对照，这一段没有画面。", flush=True)
                    receive_control(run, str(IPv4Address(address)), int(port),
                                    connect_seconds=20 if use_manual else 3,
                                    board_closed=control_closed)
                    if not run.summary["tcp_control"]["complete"]:
                        print("纯网络对照未完成。调试台会继续检查能否打开画面。", flush=True)
                elif event.startswith("HCV_VICAP_DETECT,"):
                    _, elapsed, frames, errors = event.split(",")
                    duration = int(elapsed.split("=")[1])
                    count = int(frames.split("=")[1])
                    failures = int(errors.split("=")[1])
                    run.summary["detector_sample"] = {
                        "channel": 1, "elapsed_ms": duration, "frames": count,
                        "errors": failures, "fps": count * 1000 / duration,
                    }
                    stamp = round(datetime.fromisoformat(received_at).timestamp() * 1000)
                    detector_samples.append((stamp, duration, count, failures))
                elif event == "HCV_CH0_CONFIG_READY" and vicap and not simulation:
                    run.summary["rtsp_ready_at"] = received_at
                    print("板子已启动图传，正在等首个关键帧。", flush=True)
                elif endpoint and (event == "HCV_CH0_CONFIG_READY"
                                   or (vicap and event.startswith("HCV_RTSP_FIRST_IDR,"))):
                    if "vlc" in run.summary["stages"] or "single-receiver" in run.summary["stages"]:
                        continue
                    if direct and (simulation or plan.get("hubei")):
                        run.summary["rtsp_ready_at"] = received_at
                    if event.startswith("HCV_RTSP_FIRST_IDR,"):
                        _, elapsed, pts = event.split(",")
                        run.summary["rtsp_first_idr"] = {
                            "serial_at": received_at, "elapsed_ms": int(elapsed.split("=")[1]),
                            "pts_ms": int(pts.split("=")[1]),
                        }
                    if not vicap and not direct and not wait_for_board(
                        run, str(IPv4Address(address)), int(port), seconds=20 if use_manual else 3
                    ):
                        run.summary["network"]["connected"] = "未确认"
                        if run.summary["network"]["board_reachable"]:
                            run.summary["message"] = "板子有回应，但画面连接没有打开。"
                        else:
                            run.summary["message"] = "没收到板子的回应，请确认已经连到板子的网络。"
                        print(run.summary["message"], flush=True)
                        continue
                    print("正在录制。画面窗口可能需要几秒才打开。", flush=True)
                    trace = nullcontext(endpoint)
                    if vicap and not direct:
                        run.summary["rtp_arrival"] = {}
                        trace = rtp_trace.trace(endpoint, run.summary["rtp_arrival"], run.clean)
                    with trace as player_endpoint:
                        if plan.get("receiver_ab"):
                            from station.bench.player_compare import capture
                            capture(run, vlc, player_endpoint, plan, simulation)
                        else:
                            receive(run, vlc, player_endpoint, plan, simulation)
                    network = run.summary["network"]
                    if direct:
                        observed = bool(run.summary["capture"].get("transport_observed"))
                        network.update(rtsp_port_open=observed, board_reachable=observed,
                                       reachability_checked_at=now())
                    elif vicap:
                        port_open = run.summary["rtp_arrival"].get("upstream_connected", False)
                        network.update(rtsp_port_open=port_open, reachability_checked_at=now())
                        network["board_reachable"] = port_open or run.summary.get(
                            "tcp_control", {}
                        ).get("complete", False)
                    network["connected"] = (
                        ("环回模拟" if simulation else "板子") if network.get("rtsp_port_open") else "未确认"
                    )
                    if not run.summary["capture"].get("frames"):
                        run.summary["message"] = "本轮没收到可播放的画面，失败位置已写入日志。"
                        print(run.summary["message"], flush=True)
                    print("录制已结束。请等板子完成清理，期间先别拔线。", flush=True)
                elif event == "HCV_CH0_CONFIG_CLEANUP":
                    state["cleanup"] = True
                elif event.startswith("HCV_RAW_CLIP_SAVED,"):
                    _, size, start, end, digest, *timing = event.split(",")
                    run.summary["board_raw_clip"] = {
                        "bytes": int(size), "board_start_ms": int(start), "board_end_ms": int(end), "sha256": digest,
                    }
                    if timing:
                        run.summary["board_raw_clip"]["sd_write_ms"] = int(timing[0])
                elif event.startswith("HCV_CLIP_RESUMED,"):
                    _, elapsed, previous, current, pause = event.split(",")
                    run.summary["board_clip_resume"] = {
                        "board_elapsed_ms": int(elapsed.split("=")[1]),
                        "previous_pts_ms": int(previous.split("=")[1]),
                        "next_pts_ms": int(current.split("=")[1]),
                        "write_start_to_next_send_ms": int(pause.split("=")[1]),
                    }
                elif event.startswith("HCV_RAW_CLIP_"):
                    run.summary["board_raw_clip"] = {"error": event}
                elif event == "HCV_MEDIA_SESSION_ALREADY_USED":
                    run.summary["message"] = "板子这次上电用过相机，完全断电 10 秒后才能重新测试。"
        finally:
            if process.poll() is None:
                try:
                    process.wait(timeout=5 if not simulation else 2)
                except subprocess.TimeoutExpired:
                    process.terminate()
            code = process.wait(timeout=5)
            worker.join(timeout=5)
            staging.update(ended=now(), exit_code=code, cleanup=state["cleanup"])
            capture_detector_rate(run, detector_samples)
            if vicap and run.summary.get("rtsp_ready_at") and "vlc" not in run.summary["stages"]:
                run.summary["message"] = "板端没有确认首个 H.264 关键帧，本轮未打开播放器。"
                print(run.summary["message"], flush=True)
    return code == 0 and state["cleanup"]


def passed(run, plan, clean_board):
    capture = run.summary["capture"]
    criteria = plan["criteria"]
    if plan.get("direct_rtsp"):
        detector = run.summary.get("detector_during_playback", {})
        return bool(
            clean_board and run.summary["network"].get("restored", True)
            and capture.get("exit_code") == 0 and not capture.get("client_end_reason")
            and capture.get("transport_observed") == ["tcp"]
            and (capture.get("width"), capture.get("height")) == (plan["width"], plan["height"])
            and capture.get("video_track_seconds", 0) >= plan["seconds"]
            and detector.get("elapsed_ms", 0) >= (plan["seconds"] - 5) * 1000
            and detector.get("errors") == 0 and 85.5 <= detector.get("fps", 0) <= 94.5
        )
    return bool(
        clean_board and run.summary["network"].get("restored", True)
        and capture.get("exit_code") == 0
        and capture.get("transport_observed") == [plan["transport"]]
        and (capture.get("width"), capture.get("height")) == (plan["width"], plan["height"])
        and capture.get("seconds", 0) >= criteria["min_duration_ratio"] * plan["seconds"]
        and capture.get("frames", 0) >= criteria["min_frame_ratio"] * plan["fps"] * plan["seconds"]
        and (not plan.get("firmware_encoder") or (
            Fraction(capture.get("r_frame_rate", "0")) == plan["fps"]
            and capture.get("fps", 0) <= 31.5
            and (plan.get("latency") or capture.get("motion_snapshot"))
        ))
        and (not plan.get("vicap_stream_limit") or (
            run.summary.get("tcp_control", {}).get("complete") is True
            and run.summary.get("detector_during_rtp", {}).get("elapsed_ms", 0) >= 55_000
            and 85.5 <= run.summary.get("detector_during_rtp", {}).get("fps", 0) <= 94.5
            and run.summary.get("rtp_arrival", {}).get("packets", 0) > 0
        ))
    )


def choose(ask):
    presets = [validate_plan(json.loads(path.read_text(encoding="utf-8")))
               for path in sorted((HERE / "plans").glob("*.json"))]
    if not presets:
        raise RuntimeError("方案文件夹里没有方案。")
    print("图传调试台\n请选择这次要试的方案：")
    for index, plan in enumerate(presets, start=1):
        print(f"{index}. {plan['title']}（{plan['width']}×{plan['height']}，录像 {plan['seconds']} 秒）")
    print(f"{len(presets) + 1}. 自由调试\n0. 退出")
    while True:
        choice = ask("请输入编号，按回车 > ").strip()
        if choice == "0":
            return None
        if choice.isdecimal() and 1 <= int(choice) <= len(presets) + 1:
            return free_plan(presets[0], ask) if int(choice) > len(presets) else presets[int(choice) - 1]
        print("请输入上面列出的编号。")


def main(args, ask=input, selected_plan=None):
    original = None
    simulated = args.simulate is not None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output_root = args.runs.resolve()
    run = Run(output_root / (stamp + "-preflight"), {"id": "preflight", "title": "开跑前自检"},
              simulated)
    run.reset_capture = getattr(args, "reset_capture", None)
    print("图传调试台正在自检。请先接好板子，并关闭 CanMV。" if not simulated
          else "图传调试台正在检查电脑上的模拟工具。", flush=True)
    try:
        vlc, ffmpeg, mediamtx = tools(*(args.simulate or ()))
        wifi = {} if simulated else config_wifi()
        if not simulated:
            run.private.update((wifi["ssid"], wifi["password"]))
            previous = preflight(run)
    except (Exception, KeyboardInterrupt) as error:
        message = (run.clean(str(error)) if isinstance(error, RuntimeError)
                   else "开跑前自检未通过。请把自检记录留给我检查。")
        run.summary["message"] = message
        run.line("wizard.log", f"{now()} {type(error).__name__}: {error}")
        run.finish()
        print(message)
        print(f"自检记录保存在 {run.directory}")
        return 1
    print("自检通过，可以选方案。")
    run.summary["preflight_passed"] = True
    plan = choose(ask) if selected_plan is None else validate_plan(selected_plan)
    if plan is None:
        run.summary["message"] = "已退出，未启动录像。"
        run.finish()
        return 0
    destination = output_root / (stamp + "-" + plan["id"])
    if run.directory.resolve().parent != output_root or destination.resolve().parent != output_root:
        raise RuntimeError("记录文件夹位置不对。请联系我检查。")
    run.directory.rename(destination)
    run.directory = destination
    run.summary["plan"] = plan
    clean_board = False
    raw_clip_path = None
    try:
        if plan.get("firmware_encoder") and not find_ffprobe():
            raise RuntimeError("电脑缺少核对录像帧率的组件。请联系我补齐后再跑这个方案。")
        for step in plan["steps"]:
            print(step)
        if simulated:
            print("本次用电脑生成测试画面，无需接板子或切换网络。")
        if ask("准备好并同意继续，请输入 1；输入其他内容结束 > ").strip() != "1":
            run.summary["message"] = "已取消。"
            return 0
        if not simulated:
            try:
                original = wlan.snapshot()
            except (OSError, subprocess.TimeoutExpired):
                original = None
            if original is None:
                if getattr(args, "field", False):
                    raise RuntimeError("无法核对原 Wi-Fi，请恢复原网络后重新打开向导。")
                print("Windows 没有提供当前的 Wi-Fi 状态，电脑无法自动切换并切回。")
                print("可在「设置 → 隐私和安全性 → 位置」开启位置服务后重新打开调试台。")
                print("也可以现在手动切换，录完后请自己重新连接原来的 Wi-Fi。")
                if ask("同意手动切换请输入 1；输入其他内容结束 > ").strip() != "1":
                    run.summary["message"] = "已取消，未启动相机。"
                    return 0
                run.summary["network"]["before"] = "Windows 未提供 Wi-Fi 状态"
            else:
                if getattr(args, "field", False) and original != args.original:
                    raise RuntimeError("当前网络与自检时不同，请先切回开始时的 Wi-Fi。")
                run.private.update(value for value in original if value)
                run.summary["network"]["before"] = (
                    "原 Wi-Fi 已连接" if original[1] else "原来未连接 Wi-Fi"
                )
            prepare_board_files(run, previous)
            print("开始板子测试。请留意接下来的连接和画面提示。", flush=True)
        else:
            wifi = {}
            run.summary["network"]["before"] = "环回模拟"
        context = (
            localhost_source(ffmpeg, mediamtx, run.directory, ("tcp", "udp"),
                             size=f"{plan['width']}x{plan['height']}", fps=plan["fps"],
                             redact=run.clean) if simulated else nullcontext()
        )
        with context as endpoint:
            if simulated:
                command, source = simulated_board(endpoint, plan["seconds"]), None
            else:
                timeout = plan["seconds"] + (
                    197 if plan.get("vicap_stream_limit") and not plan.get("direct_rtsp") else 77)
                command = BOARD + ["run", "-", "--stream", "--timeout", str(timeout)]
                if plan.get("direct_rtsp"):
                    raw_clip_path = f"/sdcard/hcv/{stamp}-encoder.h264"
                source = board_entry(plan, wifi, raw_clip_path)
            if plan.get("direct_rtsp") and not simulated:
                import winsound
                if ask("镜头已对准屏幕左半边？输入「拍」后听五声低音，随后高音启动采集 > ").strip() != "拍":
                    run.summary["message"] = "用户取消拍摄。"
                    return 0
                for _ in range(5):
                    winsound.Beep(440, 150)
                    time.sleep(0.85)
                winsound.Beep(1100, 200)
            clean_board = stream(run, command, source, plan, wifi, original, vlc, simulated)
            if clean_board and not simulated:
                code, _, _ = run.command("board-after", BOARD + ["info"], timeout=15)
                clean_board = code == 0
    except (Exception, KeyboardInterrupt) as error:
        run.summary["message"] = run.clean(str(error)) if isinstance(error, RuntimeError) else (
            "调试中止。详细信息已记入本次日志。")
        run.line("wizard.log", f"{now()} {type(error).__name__}: {error}")
        print(run.summary["message"])
    finally:
        if original is not None:
            run.summary["network"]["restore_started"] = now()
            try:
                restored = wlan.restore(original, diagnostics=run.summary["network"])
            except subprocess.TimeoutExpired:
                restored = False
                run.summary["network"]["restore_reason"] = "windows_command_timeout"
            except OSError:
                restored = False
                run.summary["network"]["restore_reason"] = "windows_command_unavailable"
            run.summary["network"]["restored"] = restored
            run.summary["network"]["restore_ended"] = now()
            if not restored:
                print(wlan.MANUAL_RESTORE, flush=True)
                run.summary["network"]["manual_restore_prompt_shown"] = True
        elif run.summary["network"].get("manual"):
            print("录制已结束，请从右下角网络图标切回原来的 Wi-Fi。", flush=True)
            run.summary["network"]["manual_restore_prompt_shown"] = True
        run.summary["passed"] = passed(run, plan, clean_board)
        if plan.get("latency"):
            run.summary["recording_passed"] = run.summary["passed"]
            run.summary["passed"] = False
            run.summary["latency"] = latency.result([])
        if plan.get("firmware_encoder") and not plan.get("latency"):
            run.summary["recording_passed"] = run.summary["passed"]
            run.summary["passed"] = False
            run.summary["motion_quality"] = None
        run.finish()
    raw = run.summary.get("board_raw_clip", {})
    if raw_clip_path and raw.get("bytes") and not clean_board:
        raw.update(board_path=raw_clip_path, downloaded=False)
        print("板端已确认保存原片。USB 会话未正常退出，文件路径已记入报告，供恢复后取回。")
        run.finish()
    if raw_clip_path and clean_board and raw.get("bytes"):
        original_clip = run.directory / "board-encoder.h264"
        print("正在通过 USB 取回板端原始码流。", flush=True)
        code, _, _ = run.command("board-raw-clip", BOARD + [
            "get", raw_clip_path, str(original_clip), "--chunk-size", "4096",
        ], timeout=180)
        raw["downloaded"] = bool(code == 0 and original_clip.is_file()
                                 and original_clip.stat().st_size == raw["bytes"]
                                 and sha256(original_clip.read_bytes()).hexdigest() == raw["sha256"])
        if not raw["downloaded"]:
            print("原始码流未取回，板上文件已保留供重试。", flush=True)
        run.finish()
    if getattr(args, "field", False):
        return int(not run.summary.get("recording_passed"))
    try:
        if plan.get("latency"):
            run.summary["latency"] = latency.read_samples(run.directory, ask)
            run.summary["passed"] = (run.summary["recording_passed"]
                                     and run.summary["latency"]["passed"])
        if plan.get("firmware_encoder") and not plan.get("latency") and run.summary["capture"].get("frames"):
            rating = ask("移动时的画质请评 1–5 分，5 分是没有明显色块，1 分是很严重。回车跳过 > ").strip()
            if rating in ("1", "2", "3", "4", "5"):
                quality = int(rating)
                run.summary["motion_quality"] = quality
                run.summary["passed"] = run.summary["recording_passed"] and quality >= 4
        print("最后请记一下你看到的情况。看不到或不确定也可以直接按回车。")
        for name, prompt in (
            ("画面", "看到画面了吗？"),
            ("卡顿", "画面有停住或跳动吗？"),
            ("中断", "如果中途断了，大约第几秒？"),
            ("备注", "还有什么想记下来？"),
        ):
            run.summary["observations"][name] = ask(prompt + " > ").strip()
    except (EOFError, KeyboardInterrupt):
        pass
    run.finish()
    print(f"本次记录已保存到 {run.directory}")
    return int(not run.summary["passed"])


def player(arguments):
    executable, url, recording, seconds, transport, cache_ms, show_video, *ready = arguments
    hubei = ready[1:] == ["hubei"]

    def ended(elapsed, state):
        event = {"client_end_seconds": round(elapsed, 3), "client_end_at": now(),
                 "client_end_reason": "eof" if state == 6 else "player_error"}
        print("HCV_CLIENT_ENDED " + json.dumps(event), flush=True)

    def playing():
        event = {"client_playing_at": now()}
        if ready and ready[0] and not hubei:
            delta = (datetime.fromisoformat(event["client_playing_at"]) - datetime.fromisoformat(ready[0])).total_seconds()
            event.update(seconds_from_board_ready=round(delta, 3), overlap_ready=0 <= delta <= 20)
            status = "满足" if event["overlap_ready"] else "不足"
            message = f"录像已开始，距板端就绪约 {delta:.1f} 秒，原始短片重叠窗口{status}。"
            if window is not None:
                window.title.configure(text=message)
                window.root.update_idletasks()
            (Path(recording).parent / "playback-start.txt").write_text(message, encoding="utf-8")
        print("HCV_CLIENT_PLAYING " + json.dumps(event), flush=True)

    window = latency.ClockWindow(Path(recording).parent) if show_video == "2" else None
    try:
        version = record_with_libvlc(
            Path(executable), url, Path(recording), seconds=int(seconds), diagnostic_log=False,
            show_video=show_video != "0", transport=transport, cache_ms=int(cache_ms),
            observation=ended, window=window, on_playing=playing,
            **({"hubei": True} if hubei else {}),
        )
    finally:
        if window is not None:
            window.close()
    print("HCV_VLC_VERSION " + version, flush=True)
    print("HCV_CLIENT_DONE " + json.dumps({"client_done_at": now()}), flush=True)


@contextmanager
def only_instance(directory):
    if sys.platform != "win32":
        yield
        return
    import msvcrt

    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".wizard.lock").open("a+b") as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("另一个调试台窗口已打开，请先关闭它。") from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", nargs=2, type=Path, metavar=("FFMPEG", "MEDIAMTX"))
    parser.add_argument("--runs", type=Path, default=HERE / "runs")
    parser.add_argument("--link-check", action="store_true", help="run wireless tests without starting the camera")
    parser.add_argument("--udp-only", action="store_true", help="run only the two UDP link trials")
    parser.add_argument("--burst", action="store_true", help="compare five board-to-PC burst patterns")
    parser.add_argument("--automatic-only", action="store_true",
                        help="run a local link check without prompts and verify Wi-Fi restoration")
    parser.add_argument("--field", action="store_true", help="run the operator-led offline M3 field session")
    parser.add_argument("--hubei", action="store_true", help="run the AP then conditional STA reproduction")
    parser.add_argument("--hubei-m1", action="store_true", help="measure CHN1 alongside approved physical AP video")
    parser.add_argument("--simulate-cut", choices=("ap", "both"), help="end selected Hubei simulated sources early")
    parser.add_argument("--player", nargs="+", help=argparse.SUPPRESS)
    parser.add_argument("--receive-low-delay", metavar="RTSP_URL", help="从已启动的视频源单路接收，同时显示和录像")
    parser.add_argument("--compare-players", action="store_true", help="v1.8 同次到场分别测试 VLC 和单路低延迟接收")
    parser.add_argument("--optical", action="store_true", help="两轮接收器对照都显示电脑时钟并保存对照截图")
    parser.add_argument("--receive-seconds", type=int)
    parser.add_argument("--framedrop", action="store_true", help="低延迟播放器可主动跳过迟到帧")
    parser.add_argument("--rtp-diagnostic", action="store_true", help="本轮通过中继记录 RTP 到达时间，对照两端使用相同路径")
    args = parser.parse_args()
    if (args.receive_low_delay or args.compare_players) and (
            args.hubei or args.hubei_m1 or args.field or args.link_check or args.burst or args.player
            or args.udp_only or args.automatic_only or args.simulate_cut):
        parser.error("接收器测试需要独立运行")
    if args.receive_low_delay and (args.compare_players or args.simulate):
        parser.error("指定 RTSP 源时不能同时使用其他测试模式")
    if args.receive_seconds is not None and (not args.receive_low_delay or args.receive_seconds <= 0):
        parser.error("--receive-seconds 仅用于已指定 RTSP 源的正时长测试")
    if args.framedrop and not (args.receive_low_delay or args.compare_players):
        parser.error("--framedrop 仅用于低延迟接收器")
    if args.rtp_diagnostic and not (args.receive_low_delay or args.compare_players):
        parser.error("--rtp-diagnostic 需要独立接收或播放器对照模式")
    if args.optical and not args.compare_players:
        parser.error("--optical 需要播放器对照模式")
    if args.hubei_m1 and (args.simulate or args.hubei or args.field or args.link_check or args.player):
        parser.error("--hubei-m1 requires a standalone physical session")
    if args.simulate_cut and not (args.hubei and args.simulate):
        parser.error("--simulate-cut requires --hubei --simulate")
    if (args.udp_only or args.automatic_only or args.burst) and (not args.link_check or args.player or args.simulate):
        parser.error("link trial options require a physical --link-check")
    if args.burst and (args.udp_only or args.field):
        parser.error("--burst requires the five-trial link check")
    if args.player:
        player(args.player)
    else:
        try:
            with only_instance(args.runs):
                if args.receive_low_delay:
                    from station.bench.low_delay import receiver_cli
                    sys.exit(receiver_cli(args.receive_low_delay, args.runs, args.receive_seconds or 65,
                                          args.framedrop, diagnostic=args.rtp_diagnostic))
                if args.compare_players:
                    from station.bench.player_compare import wizard
                    sys.exit(wizard(args))
                if args.hubei_m1:
                    from station.bench.hubei_m1 import wizard
                    sys.exit(wizard(args))
                if args.hubei:
                    from station.bench.hubei import wizard
                    sys.exit(wizard(args))
                if args.field:
                    from station.bench.field import field
                    sys.exit(field(args))
                if args.link_check:
                    from station.bench.link_check import main as link_main
                    sys.exit(link_main(args, udp_only=args.udp_only, automatic_only=args.automatic_only))
                sys.exit(main(args))
        except RuntimeError as error:
            print(str(error))
            sys.exit(1)
        except (OSError, ValueError) as error:
            print(f"调试台无法启动。请联系我检查方案文件和播放器。错误类型：{type(error).__name__}")
            sys.exit(1)
