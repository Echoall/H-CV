from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from k230.hcv import video_observation
from station.bench import video_health


def test_board_heartbeat_reports_completed_operations(monkeypatch, capsys):
    clock = [0]
    monkeypatch.setattr(video_observation.time, "ticks_ms", lambda: clock[0], raising=False)
    monkeypatch.setattr(video_observation.time, "ticks_diff", lambda a, b: a - b, raising=False)
    calls = []

    class Server:
        def __init__(self):
            self.encoder = SimpleNamespace(GetStream=lambda ch, data: int(data is None),
                                           ReleaseStream=lambda ch, data: calls.append("release"))
            self.server = SimpleNamespace(rtspserver_sendvideodata_byphyaddr=lambda *args: calls.append("send"))

        def _stream_loop(self):
            for stamp in (0, 900, 1001):
                clock[0] = stamp
                assert self.encoder.GetStream(0, None) == 1
                assert self.encoder.GetStream(0, "frame") == 0
                self.server.rtspserver_sendvideodata_byphyaddr("video", 1, 4, 1000)
                self.encoder.ReleaseStream(0, "frame")

    beat = video_observation.MainHeartbeat()
    beat.tick()
    video_observation.observed_server(Server)._stream_loop()
    beat.tick()
    assert calls == ["send", "release"] * 3
    assert capsys.readouterr().out.splitlines() == ["HCV_VIDEO_ENCODER,3,3,3", "HCV_VIDEO_MAIN,2"]


@pytest.mark.parametrize("case,expected", [
    ("complete", True), ("stop_early", False), ("encoder_gap", False), ("stale", False),
    ("main_missing", False), ("abnormal_exit", False), ("relay_still_connected", False),
    ("missing_cleanup", False), ("usb_lost", False),
])
def test_post_exit_requires_both_live_threads_and_orderly_stop(tmp_path, case, expected):
    def stamp(t):
        return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds")
    cap = dict(receiver_exit_at=stamp(100), exit_code=0)
    diagnostic = case == "relay_still_connected"
    if diagnostic:
        cap["upstream_closed_ns"] = 104_000_000_000
    if case == "abnormal_exit":
        cap["client_end_reason"] = "eof"
    run = SimpleNamespace(directory=tmp_path, summary=dict(plan=dict(rtp_diagnostic=diagnostic),
                                                          capture=cap, simulation=True))
    if case == "usb_lost":
        run.summary["usb_presence"] = {"first_absent_at": stamp(105)}
    lines = []
    for i in range(1, 16):
        if case != "main_missing":
            lines.append(f"{stamp(100+i)} HCV_VIDEO_MAIN,{i}")
        if case != "encoder_gap" or i != 9:
            lines.append(f"{stamp(100+i)} HCV_VIDEO_ENCODER,{i},{0 if case == 'stale' else i},{i}")
    lines.append(f"{stamp(114.999 if case == 'stop_early' else 115)} HCV_SERVICE_STOP_BEGIN")
    if case != "missing_cleanup":
        lines.append(f"{stamp(120)} HCV_CH0_CONFIG_CLEANUP")
    (tmp_path / "board.serial.log").write_text("\n".join(lines), encoding="utf-8")
    assert video_health.assess(run, True) is expected
