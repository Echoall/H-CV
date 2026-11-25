import json
from pathlib import Path
import socket
from subprocess import CompletedProcess
import sys
from threading import Thread
from types import SimpleNamespace

import pytest

from k230.hcv import tcp_control
from station.bench import bench, latency, link_check, rtp_trace, wlan
from tools.board import m3_link_check


def baseline():
    path = bench.HERE / "plans/p1-ap-baseline.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_all_plans_validate_and_required_fields():
    for path in (bench.HERE / "plans").glob("*.json"):
        plan = bench.validate_plan(json.loads(path.read_text(encoding="utf-8")))
        assert plan["id"] == path.stem
    plan = baseline()
    del plan["criteria"]
    with pytest.raises(ValueError):
        bench.validate_plan(plan)


@pytest.mark.parametrize("field,value", [
    ("fps", 31), ("bitrate_kbps", 499), ("height", 541), ("gop", 61),
    ("transport", "other"), ("caching_ms", 99), ("seconds", 61), ("version", True),
    ("sensor_mode", [1280, 720, 30]), ("sensor_mode", [1920, 1080, 30.0]),
])
def test_invalid_boundaries(field, value):
    with pytest.raises(ValueError):
        bench.validate_plan({**baseline(), field: value})


def test_free_mode_uses_same_bounds(capsys):
    answers = iter(["2", "2", "31", "", "", "", "", "5",
                    "2", "2", "15", "500", "10", "100", "5"])
    result = bench.free_plan(baseline(), lambda _: next(answers))
    assert result["width"] == 640
    assert result["transport"] == "udp"
    assert result["fps"] == 15
    assert result["seconds"] == 5
    assert "重新填写" in capsys.readouterr().out


def test_redaction_before_persistence(tmp_path):
    run = bench.Run(tmp_path / "run", baseline(), False)
    ssid, password = "测试&网络", "p&!<PRIVATE>123"
    run.private.update((ssid, password, "HomeNet-A", "fps"))
    text = (f"{ssid} 测试&amp;网络 {password} HomeNet-A 192.0.2.12 "
            "fe80::6140:1234:5678:42%15 2001:db8::1 AA:BB:CC:11:22:33 "
            "66-77-88-00-AA-BB")
    run.line("example.log", text)
    run.summary["observations"]["备注"] = text
    run.finish()
    for name in ("example.log", "summary.json", "summary.md"):
        document = (run.directory / name).read_text(encoding="utf-8")
        for secret in (ssid, "测试&amp;网络", password, "HomeNet-A", "192.0.2.12", "fe80:",
                       "2001:db8", "AA:BB", "66-77-88"):
            assert secret not in document
    assert json.loads((run.directory / "summary.json").read_text(encoding="utf-8"))["plan"]["fps"] == 30


def test_wlan_connect_restore_and_private_profile_cleanup():
    current = ["Home Synthetic", "Home Saved Profile"]
    commands = []
    profiles = []

    def command(*args):
        commands.append(args)
        if args[0] == "show":
            details = f"\n    Name : Wi-Fi\n    SSID : {current[0]}\n    Profile : {current[1]}\n"
            return CompletedProcess(args, 0, details)
        if args[0] == "add":
            profile = Path(args[2].partition("=")[2])
            assert "example&amp;pass" in profile.read_text(encoding="utf-8")
            profiles.append(profile)
        elif args[0] == "connect":
            current[:] = [args[2].partition("=")[2], args[1].partition("=")[2]]
        return CompletedProcess(args, 0, "")

    original = wlan.snapshot(command)
    assert original == ("Wi-Fi", "Home Synthetic", "Home Saved Profile")
    assert wlan.connect(original, "Board Synthetic", "example&pass", command=command)
    assert all(not path.exists() for path in profiles)
    assert wlan.restore(original, command=command)
    assert commands[-2][1] == "name=Home Saved Profile"


def test_wlan_failure_gives_manual_instructions(capsys, monkeypatch, tmp_path):
    def access_denied(*args):
        return CompletedProcess(args, 1, "")

    assert wlan.snapshot(access_denied) is None
    monkeypatch.setattr(wlan, "connect", lambda *_: False)
    run = bench.Run(tmp_path / "run", baseline(), False)
    wifi = {"ssid": "Bench-Test", "password": "dummy-pass"}
    assert not bench.connect_board(run, ("Wi-Fi", "original", "saved"), wifi)
    bench.show_board_network(wifi)
    assert "右下角网络图标" in capsys.readouterr().out
    assert run.summary["network"]["automatic_switch"] is False


def test_restore_failure_records_reason_and_displays_manual_prompt(capsys, tmp_path, monkeypatch):
    original = ("Wi-Fi", "Original Synthetic", "Saved Synthetic")
    failure = lambda *args: CompletedProcess(args, 1, "")
    monkeypatch.setattr(bench, "tools", lambda: ("vlc.exe", None, None))
    monkeypatch.setattr(bench, "config_wifi", lambda: {"ssid": "Board Synthetic",
                                                     "password": "dummy-pass"})
    monkeypatch.setattr(wlan, "snapshot", lambda command=None: original if command is None else None)
    restore = wlan.restore
    monkeypatch.setattr(wlan, "restore", lambda state, **kw: restore(state, command=failure, **kw))
    monkeypatch.setattr(bench, "preflight", lambda run: {})
    monkeypatch.setattr(bench, "prepare_board_files", lambda *args: None)
    monkeypatch.setattr(bench, "stream", lambda *args: False)
    answers = iter(["1", "1", "", "", "", ""])
    args = SimpleNamespace(runs=tmp_path, simulate=None)
    assert bench.main(args, lambda _: next(answers)) == 1
    result = json.loads(next(tmp_path.glob("*/summary.json")).read_text(encoding="utf-8"))
    assert not result["network"]["restored"]
    assert result["network"]["restore_reason"] == "windows_rejected_network_command"
    assert result["network"]["manual_restore_prompt_shown"]
    assert wlan.MANUAL_RESTORE in capsys.readouterr().out


def test_encoder_plan_does_not_accept_90_fps_recording(tmp_path):
    plan = json.loads((bench.HERE / "plans/p6-ap-venc30.json").read_text(encoding="utf-8"))
    run = bench.Run(tmp_path / "run", plan, False)
    run.summary["capture"] = dict(
        width=960, height=540, seconds=59, frames=1770, transport_observed=["tcp"],
        exit_code=0, r_frame_rate="90/1", fps=30, motion_snapshot=True,
    )
    assert not bench.passed(run, plan, True)
    run.summary["capture"]["r_frame_rate"] = "30/1"
    assert bench.passed(run, plan, True)


def test_vicap_gate_needs_detector_network_control_and_rtp(tmp_path):
    plan = json.loads((bench.HERE / "plans/p7-ap-vicap30.json").read_text(encoding="utf-8"))
    run = bench.Run(tmp_path / "run", plan, False)
    run.summary["capture"] = dict(
        width=960, height=540, seconds=59, frames=1770, transport_observed=["tcp"],
        exit_code=0, r_frame_rate="30/1", fps=30, motion_snapshot=True,
    )
    assert not bench.passed(run, plan, True)
    run.summary["detector_during_rtp"] = {"fps": 90, "elapsed_ms": 56_000}
    run.summary["tcp_control"] = {"complete": True}
    assert not bench.passed(run, plan, True)
    run.summary["rtp_arrival"] = {"packets": 1800}
    assert bench.passed(run, plan, True)
    run.summary["detector_during_rtp"]["fps"] = 30
    assert not bench.passed(run, plan, True)


def test_detector_rate_uses_only_samples_while_receiving_rtp(tmp_path):
    run = bench.Run(tmp_path / "run", baseline(), False)
    run.summary["rtp_arrival"] = {"first_at_ms": 2002, "last_at_ms": 5000}
    bench.capture_detector_rate(run, [
        (1000, 1000, 40, 0), (2000, 2000, 90, 0), (3000, 3000, 180, 0),
        (4000, 4000, 270, 0), (5000, 5000, 360, 0), (6000, 6000, 390, 0),
    ])
    assert run.summary["detector_during_rtp"] == {
        "channel": 1, "elapsed_ms": 2000, "frames": 180, "errors": 0, "fps": 90,
    }


def test_rtp_trace_reassembles_rtsp_body_and_interleaved_frames():
    header = b"RTSP/1.0 200 OK\r\nContent-Length: 5\r\n\r\n"
    frame = b"$\x00\x00\x0c" + bytes(range(12))
    buffer = bytearray(header + b"$\x00")
    assert list(rtp_trace.messages(buffer)) == []
    buffer.extend(b"SDP" + frame[:3])
    assert list(rtp_trace.messages(buffer)) == [(header, b"$\x00SDP")]
    buffer.extend(frame[3:])
    assert list(rtp_trace.messages(buffer)) == [(frame, b"")]
    assert not buffer


def test_rtp_trace_keeps_forwarding_if_post_play_inspection_fails():
    play = b"PLAY rtsp://host/hcv RTSP/1.0\r\nCSeq: 5\r\n\r\n"
    response = b"RTSP/1.0 200 OK\r\nCSeq: 5\r\n\r\n"
    rtp = b"$\x00\x00\x0c\x80\x60\x00\x01" + bytes(8)
    unrecognized = b"x" * 70_000
    with socket.socket() as upstream:
        upstream.bind(("127.0.0.1", 0))
        upstream.listen(1)
        port = upstream.getsockname()[1]

        def serve():
            conn, _ = upstream.accept()
            with conn:
                assert conn.recv(4096) == play
                conn.sendall(response + rtp + unrecognized + rtp)

        server = Thread(target=serve, daemon=True)
        server.start()
        stats = {}
        with rtp_trace.trace(f"rtsp://127.0.0.1:{port}/hcv", stats, str) as endpoint:
            peer = int(endpoint.rsplit(":", 1)[1].split("/")[0])
            with socket.create_connection(("127.0.0.1", peer), timeout=3) as client:
                client.sendall(play)
                received = bytearray()
                while chunk := client.recv(65_536):
                    received.extend(chunk)
        server.join(3)
    assert received == response + rtp + unrecognized + rtp
    assert stats["rtp_inspection_stopped"]["reason"] == "invalid RTSP header"
    assert stats["board_bytes_after_play"] == 2 * len(rtp) + len(unrecognized)
    assert stats["closed_by"] == "board"
    assert "error" not in stats


def test_rtsp_trace_records_only_matching_describe_responses(tmp_path):
    run = bench.Run(tmp_path / "run", baseline(), False)
    run.private.add("test-network")
    records = []
    sdp = b"v=0\r\nc=IN IP4 192.0.2.1\r\ns=test-network\r\na=control:track1\r\n"
    rtp_trace._record_describe(b"DESCRIBE rtsp://example.test/hcv RTSP/1.0\r\nCSeq: 4\r\n\r\n",
                               b"", False, 10, records, run.clean)
    rtp_trace._record_describe(b"RTSP/1.0 200 OK\r\nCSeq: 3\r\n\r\n", b"", True, 20, records, run.clean)
    assert records == [{"cseq": 4, "request_at_ms": 10}]
    rtp_trace._record_describe(b"RTSP/1.0 200 OK\r\nCSeq: 4\r\n\r\n", sdp, True, 30, records, run.clean)
    assert records == [{"cseq": 4, "request_at_ms": 10, "response_at_ms": 30, "status": 200,
                        "sdp": "v=0\r\nc=IN IP4 <ipv4>\r\ns=<private>\r\na=control:track1\r\n"}]


@pytest.mark.parametrize("idr", [False, True])
def test_vicap_player_waits_for_first_idr(tmp_path, monkeypatch, idr):
    plan = json.loads((bench.HERE / "plans/p7-ap-vicap30.json").read_text(encoding="utf-8"))
    run = bench.Run(tmp_path / "run", plan, False)
    events = ["HCV_RTSP_PRIVATE,192.0.2.1,8554,hcv", "HCV_CH0_CONFIG_READY"]
    if idr:
        events.append("HCV_RTSP_FIRST_IDR,elapsed_ms=40,pts_ms=120")
    events.append("HCV_CH0_CONFIG_CLEANUP")
    command = [sys.executable, "-u", "-c", f"print({chr(10).join(events)!r})"]

    def receive(current, executable, endpoint, plan, simulated):
        assert current.summary["rtsp_first_idr"]["pts_ms"] == 120
        current.summary["stages"]["vlc"] = {}

    monkeypatch.setattr(bench, "receive", receive)
    assert bench.stream(run, command, None, plan, {}, None, "fake-vlc", False)
    assert "rtsp_ready_at" in run.summary
    assert ("vlc" in run.summary["stages"]) is idr
    if not idr:
        assert "未打开播放器" in run.summary["message"]


def test_tcp_control_delivers_verified_bytes_and_releases_port(monkeypatch, tmp_path):
    monkeypatch.setattr(tcp_control, "start", lambda _: None)
    monkeypatch.setattr(tcp_control, "tick", lambda: None)
    monkeypatch.setattr(tcp_control, "time", SimpleNamespace(
        ticks_ms=lambda: 0, ticks_add=lambda a, b: a + b,
        ticks_diff=lambda a, b: a - b, sleep_ms=lambda _: None,
    ))
    host = "127.0.0.1"
    with socket.socket() as probe:
        probe.bind((host, 0))
        port = probe.getsockname()[1]
    sender = Thread(target=tcp_control.send_control, args=(host, port), daemon=True)
    sender.start()
    run = bench.Run(tmp_path / "run", baseline(), True)
    bench.receive_control(run, host, port, connect_seconds=3)
    sender.join(5)
    assert not sender.is_alive()
    assert run.summary["tcp_control"]["complete"]
    assert run.summary["tcp_control"]["bytes"] == 11_400_000
    with socket.socket() as media_listener:
        media_listener.bind((host, port))
        media_listener.listen(1)


@pytest.mark.parametrize("protocol,direction", [
    ("tcp", "board_to_pc"), ("tcp", "pc_to_board"), ("udp", "board_to_pc"),
])
def test_link_trial_exchanges_full_payload_in_each_direction(monkeypatch, capsys, protocol, direction):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(m3_link_check, "PORT", port)
    monkeypatch.setattr(link_check, "PORT", port)
    trial = (protocol, direction, 30, 1)
    completed = []
    server = Thread(target=lambda: completed.append(m3_link_check.serve("127.0.0.1", 0, trial)),
                    daemon=True)
    server.start()
    deadline = bench.time.monotonic() + 3
    while "HCV_LINK_READY" not in capsys.readouterr().out:
        assert bench.time.monotonic() < deadline
        bench.time.sleep(0.01)
    receipt = link_check.client_trial("127.0.0.1", 0, trial)
    server.join(3)
    assert not server.is_alive()
    for result in (receipt, completed[0]):
        assert result["error"] is None and result["bytes"] == 30_000
        assert result["active_ms"] >= 900
    if protocol == "udp":
        assert receipt["missing_datagrams"] == 0


def test_board_tcp_control_retries_eagain_before_accept(monkeypatch):
    clock = [0]
    attempts = []

    def accept():
        attempts.append(clock[0])
        if clock[0] < 750:
            raise OSError(11)
        return "accepted", None

    monkeypatch.setattr(tcp_control, "tick", lambda: None)
    monkeypatch.setattr(tcp_control, "time", SimpleNamespace(
        ticks_ms=lambda: clock[0], ticks_add=lambda a, b: a + b,
        ticks_diff=lambda a, b: a - b, sleep_ms=lambda ms: clock.__setitem__(0, clock[0] + ms),
    ))
    assert tcp_control.accept_control(SimpleNamespace(accept=accept)) == "accepted"
    assert attempts[0] == 0 and attempts[-1] == 750


def test_tcp_control_rejects_a_different_service_on_the_same_port(monkeypatch, tmp_path):
    run = bench.Run(tmp_path / "run", baseline(), False)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)

        def connect(address, timeout):
            client = socket.socket()
            client.connect(address)
            peer, _ = server.accept()
            with peer:
                peer.sendall(b"RTSP/1.0 200 OK\r\n\r\n")
            return client

        monkeypatch.setattr(bench.socket, "create_connection", connect)
        bench.receive_control(run, *server.getsockname(), connect_seconds=1)
    result = run.summary["tcp_control"]
    assert not result["complete"]
    assert result["error"] == "control_banner_missing"
    assert result["bytes"] == 0


def test_distinguishes_board_reply_from_unavailable_rtsp(monkeypatch, tmp_path):
    run = bench.Run(tmp_path / "run", baseline(), False)

    def closed(*args, **kwargs):
        raise ConnectionRefusedError()

    monkeypatch.setattr(bench.socket, "create_connection", closed)
    monkeypatch.setattr(run, "command", lambda *args, **kwargs: (0, "Reply: time=1ms TTL=64", ""))
    assert not bench.wait_for_board(run, "192.0.2.8", 8554, seconds=0)
    assert run.summary["network"]["board_reachable"] is True
    assert run.summary["network"]["rtsp_port_open"] is False


def test_preflight_rejects_used_boot_before_writes(tmp_path, monkeypatch):
    run = bench.Run(tmp_path / "run", baseline(), False)

    def inventory(name, *args, **kwargs):
        assert name == "board-inventory"
        return 0, "HCV_BENCH_USED 1\n", ""

    monkeypatch.setattr(run, "command", inventory)
    with pytest.raises(RuntimeError, match="全灭 10 秒"):
        bench.preflight(run)


def test_preflight_reads_marker_then_checks_board_info(tmp_path, monkeypatch):
    run = bench.Run(tmp_path / "run", baseline(), False)
    phases = []

    def responses(name, *args, **kwargs):
        phases.append(name)
        if name == "board-inventory":
            return 0, 'HCV_BENCH_USED 0\nHCV_BENCH_HASHES {"stream.py":"expect"}\n', ""
        return 0, "board info", ""

    monkeypatch.setattr(run, "command", responses)
    assert bench.preflight(run) == {"stream.py": "expect"}
    assert phases == ["board-inventory", "board-info"]


def test_startup_fails_before_plan_if_environment_missing(tmp_path, monkeypatch):
    def missing(*args):
        raise RuntimeError("缺少播放器")

    def no_plan(*args):
        pytest.fail("the menu opened before the software check")

    monkeypatch.setattr(bench, "tools", missing)
    args = SimpleNamespace(runs=tmp_path, simulate=None)
    assert bench.main(args, no_plan) == 1
    summary = json.loads(next(tmp_path.glob("*/summary.json")).read_text(encoding="utf-8"))
    assert summary["plan"]["id"] == "preflight"
    assert summary["message"] == "缺少播放器"


def test_latency_needs_at_least_five_pairs_and_checks_maximum(tmp_path, monkeypatch):
    if sys.platform == "win32":
        monkeypatch.setattr(latency.os, "startfile", lambda path: None)
    for i in range(1, 7):
        (tmp_path / f"latency-{i:02d}.png").touch()
    answers = iter(["1", "99 122", "1000 870", "2000 1850", "3000 2760", "",
                    "5000 4730", "6000 5699"])
    measurements = latency.read_samples(tmp_path, lambda _: next(answers))
    assert measurements["sample_count"] == 5
    assert measurements["median_ms"] == 240
    assert measurements["max_ms"] == 301
    assert not measurements["passed"]
    assert not latency.result(measurements["readings"][:4])["passed"]
    assert latency.result(measurements["readings"][:4] + [{"delay_ms": 300}])["passed"]


def test_runtime_plan_reaches_board_probe_without_saving_credentials(monkeypatch):
    from k230.hcv import config

    board_config = json.loads((bench.ROOT / "k230/config.example.json").read_text(encoding="utf-8"))
    board_config["wifi"]["mode"] = "off"
    received = []
    monkeypatch.setitem(sys.modules, "hcv.config", SimpleNamespace(
        load_config=lambda directory: board_config, _merge=config._merge, _validate=config._validate,
    ))
    monkeypatch.setitem(sys.modules, "hcv.ch0_configurable_probe",
                        SimpleNamespace(run=lambda **values: received.append(values)))
    monkeypatch.setitem(sys.modules, "network", SimpleNamespace(
        AP_IF=1, WLAN=lambda mode: SimpleNamespace(
            config=lambda **values: received.append(values), ifconfig=lambda: ("192.0.2.8",),
        ),
    ))
    monkeypatch.setattr(sys, "path", sys.path.copy())
    plan = bench.validate_plan({**baseline(), "width": 640, "height": 360, "fps": 20,
                                "gop": 20, "bitrate_kbps": 850, "sensor_mode": [1920, 1080, 30]})
    wifi = {"mode": "ap", "ssid": "example-board", "password": "example-key", "channel": 6}
    exec(bench.board_entry(plan, wifi), {})
    assert received[0] == {"ssid": wifi["ssid"], "key": wifi["password"], "channel": 6}
    params = received[1]["config"]
    assert params["chn_stream"]["width"] == 640
    assert params["stream"]["gop"] == 20
    assert params["stream"]["fps"] == 20
    assert params["stream"]["bitrate_kbps"] == 850
    assert (params["sensor"]["width"], params["sensor"]["height"], params["sensor"]["fps"]) == (
        1920, 1080, 30,
    )
