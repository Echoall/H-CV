"""Offline M3 test wizard. Run with python -m station.bench.bench."""

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime
from ipaddress import ip_address, IPv4Address
import json
from pathlib import Path
from queue import Empty, Queue
import re
import socket
import subprocess
import sys
from threading import Thread
import time
from urllib.parse import quote
from xml.sax.saxutils import escape

from station.bench import wlan
from station.record_rtsp import find_vlc
from tools.analysis.m3_sta_record import decode, redact as redact_rtsp
from tools.analysis.m3_vlc_smoke import localhost_source, record_with_libvlc


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BOARD = [sys.executable, str(ROOT / "tools/board/board.py")]
MODULES = ("config.py", "guard.py", "media_session_guard.py", "stream.py",
           "ch0_configurable_probe.py")
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
    if type(plan) is not dict or set(plan) != fields:
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
    if plan["transport"] not in ("tcp", "udp"):
        raise ValueError("传输方式只支持 TCP 或 UDP")
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
        capture = safe["capture"]
        state = "达到方案门槛" if safe.get("passed") else "未达到方案门槛"
        sample = "电脑模拟" if safe["simulation"] else "板子实拍"
        observations = "\n".join(f"- {name}：{value}" for name, value in safe["observations"].items())
        restored = {True: "是", False: "否"}.get(safe["network"].get("restored"), "无需切换")
        if safe["network"].get("manual") and "restored" not in safe["network"]:
            restored = "请手动切回"
        (self.directory / "summary.md").write_text(
            f"# {safe['plan']['title']}（{sample}）\n\n"
            f"- 结果：{state}\n- 录到 {capture.get('frames', 0)} 帧，"
            f"共 {capture.get('seconds', 0):.2f} 秒。\n"
            f"- 原来的网络已恢复：{restored}\n"
            f"- 说明：{safe.get('message', '录制与清理已结束。')}\n{observations}\n",
            encoding="utf-8",
        )


def config_wifi():
    path = HERE / "config.local.json"
    if not path.exists():
        prior = ROOT / "k230/ap/config.local.json"
        if not prior.is_file():
            raise RuntimeError("缺少板子网络的本地配置，请联系我补齐后再测。")
        write_json(path, {"wifi": json.loads(prior.read_text(encoding="utf-8"))["wifi"]})
    wifi = json.loads(path.read_text(encoding="utf-8"))["wifi"]
    if (wifi.get("mode") != "ap" or not isinstance(wifi.get("ssid"), str)
            or not 1 <= len(wifi["ssid"].encode("utf-8")) <= 32
            or not isinstance(wifi.get("password"), str) or not 8 <= len(wifi["password"]) <= 63
            or not wifi["password"].isascii() or not wifi["password"].isprintable()
            or any(not c.isprintable() for c in wifi["ssid"])
            or type(wifi.get("channel")) is not int or not 1 <= wifi["channel"] <= 13):
        raise RuntimeError("板子网络的本地配置有误，请联系我检查。")
    return {key: wifi[key] for key in ("mode", "ssid", "password", "channel")}


def preflight(run):
    # Inspect the original boot marker before any media operation or board upload.
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
    from hashlib import sha256

    hashes = next(line for line in text.splitlines() if line.startswith("HCV_BENCH_HASHES "))
    previous = json.loads(hashes.split(" ", 1)[1])
    for name in MODULES:
        source = ROOT / "k230/hcv" / name
        if previous.get(name) != sha256(source.read_bytes()).hexdigest():
            code, _, _ = run.command("board-sync-" + source.stem, BOARD + [
                "put", str(source), "/sdcard/hcv/hcv/" + name,
            ], timeout=25)
            if code != 0:
                raise RuntimeError("准备板子程序失败。日志已保存，请关闭调试台并联系我。")


def board_entry(plan, wifi):
    override = {
        "wifi": wifi,
        "chn_stream": {"width": plan["width"], "height": plan["height"]},
        "stream": {"enabled": True, "port": 8554, "path": "hcv",
                   **{key: plan[key] for key in ("fps", "bitrate_kbps", "gop")}},
    }
    source = ROOT / "tools/board/m3_ch0_ap_configurable_probe.py"
    return (f"HCV_STREAM_SECONDS = {plan['seconds'] + 65}\nHCV_CONFIG_OVERRIDE = {override!r}\n"
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
    override = HERE / "config.local.json"
    settings = json.loads(override.read_text(encoding="utf-8")) if override.is_file() else {}
    vlc = settings.get("vlc") or HERE / "runtime/vlc/vlc.exe"
    if not Path(vlc).is_file():
        try:
            vlc = find_vlc()
        except FileNotFoundError:
            raise RuntimeError("这台电脑找不到播放器。请联系我补齐调试台的播放器。") from None
    return str(Path(vlc).resolve()), local_ffmpeg, local_mediamtx


def receive(run, executable, endpoint, plan, simulated):
    path = run.directory / "recording.mp4"
    code, stdout, stderr = run.command("vlc", [
        sys.executable, "-m", "station.bench.bench", "--player",
        executable, endpoint, str(path), str(plan["seconds"]), plan["transport"],
        str(plan["caching_ms"]), "0" if simulated else "1",
    ], timeout=plan["seconds"] + 16)
    media = run.summary["capture"]
    transports = set()
    for match in re.finditer(r"(?im)^Transport:\s*RTP/AVP([^;\r\n]*)", stderr):
        transports.add("tcp" if match[1].upper() == "/TCP" else "udp")
    media["transport_observed"] = sorted(transports)
    for line in stdout.splitlines():
        if line.startswith("HCV_CLIENT_ENDED "):
            event = json.loads(line.partition(" ")[2])
            media.update(event)
    media["exit_code"] = code
    if path.is_file() and path.stat().st_size > 0:
        try:
            media.update(decode(path, run.directory / "preview.png"))
        except RuntimeError:
            media["decodable"] = False
    return media


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
                reachable = True
        except OSError:
            reachable = False
        if reachable or time.monotonic() - started >= seconds:
            break
        time.sleep(0.5)
    run.summary["network"].update(board_reachable=reachable, rtsp_port_open=reachable,
                                  reachability_checked_at=now())
    return reachable


def show_board_network(wifi):
    print(wlan.MANUAL_CONNECT)
    print(f"网络名称 {wifi['ssid']}　密码 {wifi['password']}")
    print("请在 20 秒内连好。电脑可能显示无互联网，这是正常的。", flush=True)


def stream(run, command, source, plan, wifi, original, vlc, simulation):
    staging = run.summary["stages"]["board-session"] = {"started": now()}
    events = Queue()
    state = {"cleanup": False}

    def collect(pipe):
        for line in pipe:
            clean = line.strip()
            run.line("board.serial.log", f"{now()} {line}")
            if clean.startswith(("HCV_RTSP_PRIVATE,", "HCV_CH0_CONFIG_READY", "HCV_AP_READY",
                                 "HCV_MEDIA_SESSION_", "HCV_CH0_CONFIG_CLEANUP")):
                events.put(clean)
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
            while True:
                remaining = deadline - time.monotonic()
                try:
                    event = events.get(timeout=max(0, remaining))
                except Empty:
                    raise RuntimeError("板子在规定时间内没有完成，请保存日志后联系我。") from None
                if event is None:
                    break
                if event == "HCV_AP_READY":
                    run.summary["network"]["switch_started"] = now()
                    use_manual = not simulation and (
                        original is None or not connect_board(run, original, wifi)
                    )
                    run.summary["network"]["manual"] = use_manual
                    if use_manual:
                        show_board_network(wifi)
                elif event.startswith("HCV_RTSP_PRIVATE,"):
                    _, address, port, path = event.split(",", 3)
                    endpoint = f"rtsp://{IPv4Address(address)}:{int(port)}/{path}"
                elif event == "HCV_CH0_CONFIG_READY" and endpoint:
                    host = str(IPv4Address(address))
                    # A plain connection checks the listening port without making another RTSP request.
                    reachable = wait_for_board(run, host, int(port), seconds=20 if use_manual else 3)
                    if not reachable:
                        run.summary["network"]["connected"] = "未确认"
                        run.summary["message"] = "电脑没有收到板子的画面连接，请确认连的是板子的网络。"
                        print(run.summary["message"], flush=True)
                        continue
                    run.summary["network"]["connected"] = "环回模拟" if simulation else "板子"
                    print("正在录制。画面窗口可能需要几秒才打开。", flush=True)
                    receive(run, vlc, endpoint, plan, simulation)
                    print("录制已结束。请等板子完成清理，最多约一分钟，期间先别拔线。", flush=True)
                elif event == "HCV_CH0_CONFIG_CLEANUP":
                    state["cleanup"] = True
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
    return code == 0 and state["cleanup"]


def passed(run, plan, clean_board):
    capture = run.summary["capture"]
    criteria = plan["criteria"]
    return bool(
        clean_board and run.summary["network"].get("restored", True)
        and capture.get("exit_code") == 0
        and capture.get("transport_observed") == [plan["transport"]]
        and (capture.get("width"), capture.get("height")) == (plan["width"], plan["height"])
        and capture.get("seconds", 0) >= criteria["min_duration_ratio"] * plan["seconds"]
        and capture.get("frames", 0) >= criteria["min_frame_ratio"] * plan["fps"] * plan["seconds"]
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


def main(args, ask=input):
    original = None
    plan = choose(ask)
    if plan is None:
        return 0
    simulated = args.simulate is not None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run = Run(args.runs / (stamp + "-" + plan["id"]), plan, simulated)
    clean_board = False
    try:
        for step in plan["steps"]:
            print(step)
        if simulated:
            print("本次用电脑生成测试画面，无需接板子或切换网络。")
        if ask("准备好并同意继续，请输入 1；输入其他内容结束 > ").strip() != "1":
            run.summary["message"] = "已取消。"
            return 0
        vlc, ffmpeg, mediamtx = tools(*(args.simulate or ()))
        if not simulated:
            wifi = config_wifi()
            run.private.update((wifi["ssid"], wifi["password"]))
            try:
                original = wlan.snapshot()
            except (OSError, subprocess.TimeoutExpired):
                original = None
            if original is None:
                print("Windows 没有提供当前的 Wi-Fi 状态，电脑无法自动切换并切回。")
                print("可在「设置 → 隐私和安全性 → 位置」开启位置服务后重新打开调试台。")
                print("也可以现在手动切换，录完后请自己重新连接原来的 Wi-Fi。")
                if ask("同意手动切换请输入 1；输入其他内容结束 > ").strip() != "1":
                    run.summary["message"] = "已取消，未启动相机。"
                    return 0
                run.summary["network"]["before"] = "Windows 未提供 Wi-Fi 状态"
            else:
                run.private.update(value for value in original if value)
                run.summary["network"]["before"] = (
                    "原 Wi-Fi 已连接" if original[1] else "原来未连接 Wi-Fi"
                )
            preflight(run)
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
                command = BOARD + ["run", "-", "--stream", "--timeout", str(plan["seconds"] + 77)]
                source = board_entry(plan, wifi)
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
                restored = wlan.restore(original)
            except (OSError, subprocess.TimeoutExpired):
                restored = False
            run.summary["network"]["restored"] = restored
            run.summary["network"]["restore_ended"] = now()
            if not restored:
                print(wlan.MANUAL_RESTORE)
        elif run.summary["network"].get("manual"):
            print("录制已结束，请从右下角网络图标切回原来的 Wi-Fi。")
        run.summary["passed"] = passed(run, plan, clean_board)
        run.finish()
    try:
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
    executable, url, recording, seconds, transport, cache_ms, show_video = arguments

    def ended(elapsed, state):
        event = {"client_end_seconds": round(elapsed, 3), "client_end_at": now(),
                 "client_end_reason": "eof" if state == 6 else "player_error"}
        print("HCV_CLIENT_ENDED " + json.dumps(event), flush=True)

    version = record_with_libvlc(
        Path(executable), url, Path(recording), seconds=int(seconds), diagnostic_log=False,
        show_video=show_video == "1", transport=transport, cache_ms=int(cache_ms), observation=ended,
    )
    print("HCV_VLC_VERSION " + version, flush=True)


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
    parser.add_argument("--player", nargs=7, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.player:
        player(args.player)
    else:
        try:
            with only_instance(args.runs):
                sys.exit(main(args))
        except RuntimeError as error:
            print(str(error))
            sys.exit(1)
        except (OSError, ValueError) as error:
            print(f"调试台无法启动。请联系我检查方案文件和播放器。错误类型：{type(error).__name__}")
            sys.exit(1)
