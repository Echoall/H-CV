"""Run the no-media K230 AP trials and keep both endpoints' results."""

from datetime import datetime
from ipaddress import IPv4Address
import json
from queue import Empty, Queue
import re
import socket
import subprocess
from threading import Thread
import time

from station.bench import bench, wlan
from tools.board.m3_link_check import PORT, TRIALS, banner, transfer


def wlan_metrics():
    try:
        result = wlan.netsh("show", "interfaces")
    except (OSError, subprocess.TimeoutExpired):
        return {}
    fields = {}
    for key, pattern in (
        ("signal_pct", r"signal|信号|訊號"),
        ("rx_mbps", r"receive rate[^:]*|接收速率[^:]*"),
        ("tx_mbps", r"transmit rate[^:]*|传输速率[^:]*|發送速率[^:]*|发送速率[^:]*"),
    ):
        match = re.search(rf"(?im)^[ \t]*(?:{pattern})[ \t]*:[ \t]*([\d.]+)", result.stdout)
        if match:
            fields[key] = float(match[1])
    return fields


def client_trial(address, index, trial):
    protocol, direction, rate, seconds = trial
    host = address, PORT
    if protocol == "udp":
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as conn:
            conn.bind(("", 0))
            for _ in range(3):
                conn.sendto(banner(index), host)
            return transfer(conn, False, rate, seconds, host)
    deadline = time.monotonic() + 8
    while True:
        try:
            conn = socket.create_connection(host, timeout=1)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)
    with conn:
        conn.settimeout(3)
        expected = banner(index)
        greeting = bytearray()
        while len(greeting) < len(expected):
            payload = conn.recv(len(expected) - len(greeting))
            if not payload:
                break
            greeting.extend(payload)
        if greeting != expected:
            raise OSError("link trial handshake did not match")
        return transfer(conn, direction == "pc_to_board", rate, seconds)


def summarize_trial(row):
    sender, receiver = ("board", "pc") if row["direction"] == "board_to_pc" else ("pc", "board")
    sent = row.get(sender, {})
    received = row.get(receiver, {})
    elapsed_ms = received.get("active_ms", 0)
    received_bytes = received.get("bytes", 0)
    expected_bytes = row["target_kBps"] * row["seconds"] * 1000
    goodput = received_bytes / elapsed_ms if elapsed_ms else None
    row["receive_kBps"] = round(goodput, 2) if goodput is not None else None
    if any(endpoint.get("error") in (104, 10054) for endpoint in (sent, received)):
        row["rst_origin"] = "unknown"
    if row["protocol"] == "udp" and sent.get("bytes") == expected_bytes and "bytes" in received:
        row["udp_loss_pct"] = round(100 * (expected_bytes - received_bytes) / expected_bytes, 3)
    row["sustained_target"] = bool(
        not sent.get("error") and not received.get("error")
        and sent.get("bytes") == received_bytes == expected_bytes
        and sent.get("active_ms", 0) >= row["seconds"] * 1000
        and goodput is not None and goodput >= 0.95 * row["target_kBps"]
    )


def run_trials(run, wifi, original):
    jobs = run.summary["trials"] = [
        {"protocol": protocol, "direction": direction, "target_kBps": rate, "seconds": seconds}
        for protocol, direction, rate, seconds in TRIALS
    ]
    board_file = bench.ROOT / "tools/board/m3_link_check.py"
    source = f"HCV_LINK_WIFI = {wifi!r}\n" + board_file.read_text(encoding="utf-8")
    events = Queue()

    def collect(pipe):
        for line in pipe:
            received_at = bench.now()
            run.line("board.serial.log", f"{received_at} {line}")
            if line.startswith("HCV_LINK_"):
                events.put((received_at, line.strip()))
        events.put(None)

    with subprocess.Popen(bench.BOARD + ["run", "-", "--stream", "--timeout", "440"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          encoding="utf-8", errors="replace", cwd=bench.ROOT) as board:
        worker = Thread(target=collect, args=(board.stdout,), daemon=True)
        worker.start()
        try:
            with board.stdin:
                board.stdin.write(source)
            address = None
            while True:
                item = events.get(timeout=85)
                if item is None:
                    break
                received_at, message = item
                marker, _, value = message.partition(",")
                if marker == "HCV_LINK_AP":
                    address = str(IPv4Address(value))
                    if original is None or not bench.connect_board(run, original, wifi):
                        run.summary["network"]["manual"] = True
                        bench.show_board_network(wifi)
                        time.sleep(12)
                elif marker == "HCV_LINK_READY" and address:
                    index = int(value)
                    protocol, direction, rate, seconds = TRIALS[index]
                    row = jobs[index]
                    route = "板子 → 电脑" if direction == "board_to_pc" else "电脑 → 板子"
                    row["board_ready_at"] = received_at
                    row["start_at"] = bench.now()
                    print(f"{index + 1}/{len(jobs)}  {protocol.upper()}  {rate} kB/s"
                          f"  {seconds} 秒  {route}", flush=True)
                    try:
                        row["pc"] = client_trial(address, index, TRIALS[index])
                    except OSError as error:
                        row["pc"] = {"error": error.args[0] if error.args else type(error).__name__}
                    row["pc_end_at"] = bench.now()
                    row["windows_wlan"] = wlan_metrics()
                    run.finish()
                elif marker == "HCV_LINK_RESULT":
                    index, _, payload = value.partition(",")
                    row = jobs[int(index)]
                    row.update(board=json.loads(payload), board_end_at=received_at)
                    summarize_trial(row)
                    run.finish()
            return board.wait(timeout=5)
        except Empty:
            raise RuntimeError("链路体检没收到板端的后续状态。") from None
        finally:
            if board.poll() is None:
                board.terminate()
                board.wait(timeout=5)
            worker.join(timeout=5)


def main(args, ask=input):
    directory = args.runs.resolve() / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-p8-link")
    run = bench.Run(directory, {"id": "p8-link", "title": "方案 8 无线链路体检"}, False)
    original = None
    switched = False
    print("把电脑放在离 K230 约 0.5–1 米处，关闭 CanMV。若电源计划有无线适配器设置，选最高性能。")
    print("全程约 5 分钟，电脑会暂时连接板子的 Wi-Fi，此方案不打开相机。")
    if ask("布置好后输入 1 开始，其他键退出 > ").strip() != "1":
        run.summary["message"] = "用户取消，未启动链路测试。"
        run.finish()
        return 0
    try:
        wifi = bench.config_wifi()
        run.private.update((wifi["ssid"], wifi["password"]))
        code, _, _ = run.command("board-info", bench.BOARD + ["info"], timeout=15)
        if code != 0:
            raise RuntimeError("板子串口自检失败。确认 USB 和 CanMV 状态后再打开本工具。")
        try:
            original = wlan.snapshot()
        except (OSError, subprocess.TimeoutExpired):
            original = None
        if original:
            run.private.update(value for value in original if value)
        else:
            print("Windows 未提供当前网络状态。测试结束后请手动切回原 Wi-Fi。", flush=True)
        switched = True
        run.summary["board_exit_code"] = run_trials(run, wifi, original)
        jobs = run.summary["trials"]
        run.summary["results_collected"] = all("board" in row and "pc" in row for row in jobs)
        run.summary["tcp_300_kBps_60s_sustained"] = bool(jobs[-1].get("sustained_target"))
        run.summary["message"] = ("链路记录已保存，检查结果以各档收发日志为准。"
                                  if run.summary["results_collected"] else "部分档位缺少收发记录。")
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as error:
        run.summary["message"] = run.clean(str(error)) or type(error).__name__
        print(run.summary["message"], flush=True)
    finally:
        if switched and original:
            try:
                run.summary["network"]["restored"] = wlan.restore(original, diagnostics=run.summary["network"])
            except (OSError, subprocess.TimeoutExpired):
                run.summary["network"]["restored"] = False
        if switched and not run.summary["network"].get("restored"):
            print(wlan.MANUAL_RESTORE, flush=True)
        run.finish()
    print(f"本次记录已保存到 {run.directory}", flush=True)
    return int(not run.summary.get("results_collected") or run.summary.get("board_exit_code") != 0)
