import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from station.bench import bench
from station.bench import field, reset_capture


@pytest.mark.parametrize("success,expected", [(2, 2), (None, 5)])
def test_direct_retry_session(tmp_path, monkeypatch, success, expected):
    run = bench.Run(tmp_path / "field", {"seconds": 60}, True)
    run.summary["rtsp_ready_at"] = bench.now()
    attempts = []
    monkeypatch.setattr(bench.time, "sleep", lambda _: None)
    monkeypatch.setattr(bench.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=b"TTL=128"))

    def command(stage, argv, *, timeout):
        attempts.append(stage)
        run.summary["stages"][stage] = {"started": bench.now(), "ended": bench.now(), "exit_code": 1}
        file = Path(argv[3])
        if len(attempts) == success:
            file.write_bytes(b"second recording")
            return 0, 'HCV_CLIENT_PLAYING {"client_playing_at":"test"}', ""
        file.write_bytes(b"failed recording")
        return 1, "", "Sending request: SETUP rtsp://test\r\nrecvfrom() error: 10054"

    monkeypatch.setattr(run, "command", command)
    path = run.directory / "recording.mp4"
    code, _, _ = bench.direct_playback(run, ["--player", "exe", "url", str(path)], 62, "127.0.0.1")
    assert len(attempts) == expected and (code == 0) == bool(success)
    assert all(a["ping"]["echo_reply"] for a in run.summary["rtsp_attempts"])
    assert run.summary["rtsp_attempts"][0]["rtsp_stage"] == "SETUP"
    assert run.summary["rtsp_attempts"][0]["socket_error_codes"] == ["10054"]
    assert path.read_bytes() == (b"second recording" if success else b"failed recording")


def test_uac_declined_continues_to_operator_recording(tmp_path, monkeypatch):
    args = SimpleNamespace(runs=tmp_path, simulate=None, field=True)
    first_connection = ("WLAN", "lab", "lab")
    fake_ffmpeg = tmp_path / "ffmpeg.exe"
    fake_ffmpeg.touch()
    launches = []
    monkeypatch.setattr(field.bench, "tools", lambda: None)
    monkeypatch.setattr(field.bench, "config_wifi", lambda: {"ssid": "board"})
    monkeypatch.setattr(field.bench, "find_ffprobe", lambda: "probe")
    monkeypatch.setattr(field.shutil, "which", lambda _: str(fake_ffmpeg))
    monkeypatch.setattr(field.wlan, "snapshot", lambda: first_connection)
    monkeypatch.setattr(field.wlan, "netsh", lambda *_: SimpleNamespace(returncode=0))
    monkeypatch.setattr(bench.Run, "command", lambda *_a, **_k: (0, "", ""))
    times = iter((50000, 1000))
    monkeypatch.setattr(field, "uptime", lambda _: next(times))
    clock = iter((100.0, 111.0))
    monkeypatch.setattr(field, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    # The unelevated PowerShell launcher exits when the UAC elevation is declined.
    monkeypatch.setattr(reset_capture.subprocess, "Popen", lambda *_a, **_k: SimpleNamespace(poll=lambda: 1))

    def record(args, ask, selected_plan):
        launches.append(selected_plan["id"])
        args.reset_capture.start("192.0.2.1")
        assert args.reset_capture.summary["status"] == "unavailable_or_declined"

    monkeypatch.setattr(bench, "main", record)
    field.field(args, lambda _: "")
    report = json.loads((args.runs / "summary.json").read_text(encoding="utf-8"))
    assert launches == ["field-direct"] and report["restored"]
    assert report["reset_capture"]["status"] == "unavailable_or_declined"


def test_rst_summary_uses_direction_and_only_board_service(tmp_path):
    from scapy.all import Ether, IP, TCP, PcapNgWriter

    board, host = "192.0.2.1", "192.0.2.2"
    capture = tmp_path / "sample.pcapng"
    with PcapNgWriter(str(capture)) as output:
        for src, dst, sport, dport, flag, seq in [
            (board, host, 8554, 40000, "RA", 84), (host, board, 40001, 554, "R", 42),
            (board, host, 8554, 40000, "A", 85), (board, host, 80, 40000, "R", 74),
        ]:
            packet = Ether(src="00:00:5e:00:53:01", dst="00:00:5e:00:53:02") / IP(src=src, dst=dst)
            packet /= TCP(sport=sport, dport=dport, flags=flag, seq=seq)
            packet.time = 1700000000
            output.write(packet)
    result = reset_capture.summarize_packets(capture, board)
    assert [(r["direction"], r["sequence"]) for r in result] == [("from_board", 84), ("toward_board", 42)]
    assert all(r["time"] == "2023-11-14T22:13:20+00:00" for r in result)
