from datetime import datetime, timedelta, timezone
import json

import pytest

from station.bench import bench, v18_visit


@pytest.mark.parametrize("stop_before,clean,expected", [(True, True, True), (False, True, False),
                                                        (True, False, False)])
def test_stop_order_requires_video_and_confirmed_clean_exit(tmp_path, stop_before, clean, expected):
    run = bench.Run(tmp_path / "service", {"id": "v18-service-stop"}, True)
    now = datetime.now(timezone.utc)
    run.summary["capture"].update(frames=400, transport_observed=["tcp"], client_playing_at=now.isoformat(),
                                  client_end_at=(now + timedelta(seconds=10)).isoformat(), client_end_reason="eof")
    stop = now + timedelta(seconds=7 if stop_before else 12)
    (run.directory / "board.serial.log").write_text(
        f"{stop.isoformat()} HCV_SERVICE_STOP_BEGIN\n"
        f"{(stop + timedelta(seconds=1)).isoformat()} HCV_CH0_CONFIG_CLEANUP\n", encoding="utf-8")
    assert v18_visit.check_stop(run, clean) is expected


def test_cancelled_physical_stage_attempts_restore_and_records_failure(monkeypatch, tmp_path):
    original = ("adapter", "example", "profile")
    monkeypatch.setattr(bench, "tools", lambda: ("player", None, None))
    monkeypatch.setattr(bench, "config_wifi", lambda: {"ssid": "ap"})
    monkeypatch.setattr(v18_visit.wlan, "snapshot", lambda: original)
    restored = []
    monkeypatch.setattr(v18_visit.wlan, "restore", lambda net, diagnostics: restored.append(net) is None)

    def cancel(*args):
        raise RuntimeError("取消")

    monkeypatch.setattr(v18_visit.hubei, "physical", cancel)
    with pytest.raises(RuntimeError, match="取消"):
        v18_visit.service_stop(tmp_path, lambda _: "")
    record = json.loads((tmp_path / "service-stop/summary.json").read_text(encoding="utf-8"))
    assert restored == [original]
    assert record["network"]["restored"] is True and record["passed"] is False


def test_operator_can_exit_between_stages_with_saved_result(tmp_path, monkeypatch):
    root = tmp_path / "visit"
    monkeypatch.setattr(v18_visit, "service_stop", lambda folder, ask: 1)
    monkeypatch.setattr(v18_visit.m1_v18, "main", lambda *args: pytest.fail("operator stopped"))
    answers = iter(["", "q"])
    assert v18_visit.visit(root, lambda _: next(answers)) == 1
    record = json.loads((root / "visit.json").read_text(encoding="utf-8"))
    assert record["completed"] is False and record["passed"] is False
    assert len(record["stages"]) == 1 and record["stages"][0]["passed"] is False


def test_v18_parallel_timing_limit_applies_after_sixty_seconds(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from station.bench import hubei_m1
    monkeypatch.setattr(bench, "tools", lambda: ("vlc", None, None))
    monkeypatch.setattr(bench, "find_ffprobe", lambda: __file__)
    monkeypatch.setattr(bench, "config_wifi", lambda: {"ssid": "board"})
    monkeypatch.setattr(hubei_m1.shutil, "which", lambda _: __file__)
    monkeypatch.setattr(hubei_m1.wlan, "snapshot", lambda: ("adapter", "home", "profile"))
    monkeypatch.setattr(hubei_m1.wlan, "restore", lambda *args, **kwargs: True)
    def physical(run, *args):
        run.summary["capture"]["video_track_seconds"] = 61
        (run.directory / "board.serial.log").touch()
        return True
    monkeypatch.setattr(hubei_m1.hubei, "physical", physical)
    monkeypatch.setattr(hubei_m1.hubei, "assess", lambda *args: "通过")
    monkeypatch.setattr(hubei_m1, "during_display", lambda *args: dict(
        wall_us=60_000_001, serial_span_s=62, process_p95_within_6ms=False))
    assert hubei_m1.wizard(SimpleNamespace(runs=tmp_path), v18_visit=tmp_path) == 1
    record = json.loads(next(tmp_path.glob("*/summary.json")).read_text(encoding="utf-8"))
    assert record["passed"] is False
    assert "p95" in record["message"]
