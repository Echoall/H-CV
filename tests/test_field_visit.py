from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from station.bench import field_visit, low_delay, player_compare, session_review, v18_dual


def test_cancel_and_failed_sensor_leave_following_stages_untouched(tmp_path, monkeypatch):
    def unexpected(*args):
        pytest.fail("started a physical test")
    monkeypatch.setattr(field_visit.sensor_reuse, "field", unexpected)
    assert field_visit.visit(tmp_path/"cancelled", lambda _: "q") == 2
    assert json.loads((tmp_path/"cancelled/visit.json").read_text())["stages"] == []
    monkeypatch.setattr(field_visit.sensor_reuse, "field", lambda *args: 1)
    monkeypatch.setattr(field_visit.m1_v18, "main", unexpected)
    assert field_visit.visit(tmp_path/"failed", lambda _: "") == 1
    record = json.loads((tmp_path/"failed/visit.json").read_text())
    assert [(r["stage"],r["status"]) for r in record["stages"]] == [("S0a","failed")]
    assert not record["full_visit_accepted"]


@pytest.mark.parametrize("fps", (60, 90))
def test_dual_uses_driver_mode_and_the_receive_interval(monkeypatch, fps):
    mode = next(m for m in v18_dual.MODES if m[2] == fps)
    epoch = 1_000_000_000_000
    rows = [(epoch + i*1_000_000_000, 0, i) for i in range(63)]
    cap = {"arrival": low_delay.continuity(rows)}
    run = SimpleNamespace(summary={"capture":cap, "simulation":False})
    monkeypatch.setattr(v18_dual.player_compare, "evaluate", lambda *args: True)
    start = datetime.fromtimestamp(1000, timezone.utc).isoformat()
    end = datetime.fromtimestamp(1062, timezone.utc).isoformat()
    lines = [f"{start} HCV_DUAL_ORIGIN,0", f"{end} HCV_DUAL_END,{62*fps},62000000"]
    last = 0
    for n in range(60, 62*fps+1, 60):
        now = round(1_000_000*n/fps)
        lines.append(f"HCV_DUAL,{n},{now},{now-last},50,110,200,100,150,250")
        last = now
    assert not v18_dual.assess(run, True, "ffmpeg", mode, lines)
    lines.append(f"{start} find sensor gc2093 fixture, output {mode[0]}x{mode[1]}@{fps}")
    assert v18_dual.assess(run, True, "ffmpeg", mode, lines)
    assert run.summary["detection_during_receive"]["frames"] == fps*62


def test_history_cannot_prove_health_after_reconnection():
    lines = ("2026-11-28T00:00:01+08:00 HCV_CH0_CONFIG_READY\n"
             "2026-11-28T00:00:02+08:00 HCV_HUBEI_STATUS,frames=60\n"
             "2026-11-28T00:00:03+08:00 video frames=120 bytes=100")
    record = {"simulation":False, "stages":{"vlc":{"ended":"2026-11-28T00:00:04+08:00","exit_code":0}}}
    result = session_review.summarize(lines, record)
    assert result["observed"]["encoder"]["last_minus_player_exit_s"] == -1
    assert result["cleanup_markers"] == 0 and result["health_after_reconnect"] == "unverified"


def test_field_clock_places_ffplay_next_to_reference_clock(tmp_path, monkeypatch):
    events = []
    class Clock:
        video_rect = (501, 30, 500, 400)
        cancelled = False
        def tick(self, elapsed):
            events.append("tick")
        def close(self):
            events.append("close")
    monkeypatch.setattr(player_compare.latency, "ClockWindow", lambda path: Clock())
    monkeypatch.setattr(low_delay, "binaries", lambda: ("ffmpeg", "ffplay"))
    def receive(url, folder, *args, **opts):
        assert opts["display_rect"] == Clock.video_rect and not opts["diagnostic"] and opts["env"] is None
        opts["tick"]()
        return dict(reason="deadline", recorder_exit=0, player_exit=0, mp4_ready=False)
    monkeypatch.setattr(low_delay, "receive", receive)
    plan = dict(receiver_ab="ffplay", optical=True, rtp_diagnostic=False, seconds=60, framedrop=False)
    run = SimpleNamespace(directory=tmp_path, summary={"stages":{}, "capture":{}})
    player_compare.capture(run, "vlc", "rtsp://localhost:8554/cam", plan, False)
    assert events == ["tick", "close"]
