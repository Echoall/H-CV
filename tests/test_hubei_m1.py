from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from station.bench import hubei_m1 as dual


def test_simulated_ap_cannot_open_physical_wizard(tmp_path):
    directory = tmp_path / "test-hubei/ap"
    directory.mkdir(parents=True)
    (directory / "recording.mp4").touch()
    (directory / "latency-01.png").touch()
    record = dict(plan={"id": "hubei-ap"}, result="通过", passed=True, network={"restored": True})
    path = directory / "summary.json"
    path.write_text(json.dumps(dict(record, simulation=True)))
    with pytest.raises(RuntimeError, match="实板"):
        dual.wizard(SimpleNamespace(runs=tmp_path))
    path.write_text(json.dumps(dict(record, simulation=False)))
    assert dual.prior_ap(tmp_path) == path


def test_only_complete_concurrent_windows_are_counted_including_gc():
    origin = datetime.fromtimestamp(1000, timezone.utc).isoformat()
    stop = datetime.fromtimestamp(1005.1, timezone.utc).isoformat()
    lines = [f"{origin} HCV_DUAL_ORIGIN,0", f"{stop} HCV_DUAL_END,300,5100000"]
    for i, elapsed in enumerate((1, 2, 3.1, 4.1, 5.1), 1):
        stamp = datetime.fromtimestamp(1000 + elapsed, timezone.utc).isoformat()
        duration = 1_100_000 if i == 3 else 1_000_000
        lines.append(f"{stamp} HCV_DUAL,{60*i},{round(elapsed*1e6)},{duration},100,300,500,1100,1500,2400")
    cap = {"display_start_epoch": 1001.5, "display_end_epoch": 1005}
    result = dual.during_display(lines, cap)
    assert (result["frames"], result["wall_us"], result["estimated_drops"]) == (120, 2_100_000, 6)
    assert result["process_us"] == [100, 300, 500]
    assert result["after_capture_us"] == [1100, 1500, 2400]
    with pytest.raises(ValueError, match="incomplete"):
        dual.during_display(lines[:2] + lines[3:], cap)


def test_module_upload_timeout_stops_before_capture():
    run = SimpleNamespace(command=lambda *args, **kwargs: (None, "", ""))
    with pytest.raises(RuntimeError, match="模块"):
        dual.prepare(run)


def test_wizard_records_same_60_seconds_and_restores(tmp_path, monkeypatch):
    monkeypatch.setattr(dual, "prior_ap", lambda _: tmp_path / "accepted/ap/summary.json")
    monkeypatch.setattr(dual.bench, "tools", lambda: ("vlc", None, None))
    monkeypatch.setattr(dual.shutil, "which", lambda _: __file__)
    monkeypatch.setattr(dual.bench, "find_ffprobe", lambda: __file__)
    monkeypatch.setattr(dual.bench, "config_wifi", lambda: {"ssid": "board"})
    monkeypatch.setattr(dual.wlan, "snapshot", lambda: ("test", "original", "profile"))
    monkeypatch.setattr(dual.wlan, "restore", lambda *args, **kwargs: True)

    def physical(run, mode, ask, original, vlc):
        assert mode == "ap" and run.summary["plan"]["m1"]
        run.summary["capture"].update(display_start_epoch=1001, display_end_epoch=1061, video_track_seconds=61)
        origin = datetime.fromtimestamp(1000, timezone.utc).isoformat()
        stop = datetime.fromtimestamp(1062, timezone.utc).isoformat()
        lines = [f"{origin} HCV_DUAL_ORIGIN,0", f"{stop} HCV_DUAL_END,3720,62000000"]
        for i in range(1, 63):
            stamp = datetime.fromtimestamp(1063, timezone.utc).isoformat()
            lines.append(f"{stamp} HCV_DUAL,{60*i},{1000000*i},1000000,300,500,900,1300,1700,2300")
        (run.directory / "board.serial.log").write_text("\n".join(lines), encoding="utf-8")
        return True

    monkeypatch.setattr(dual.hubei, "physical", physical)
    monkeypatch.setattr(dual.hubei, "assess", lambda run, clean, ffmpeg: "通过" if clean else "失败")
    assert dual.wizard(SimpleNamespace(runs=tmp_path)) == 0
    report = json.loads(next(tmp_path.glob("*-hubei-m1/summary.json")).read_text(encoding="utf-8"))
    assert report["m1_during_display"]["frames"] == 3600
    assert report["network"]["restored"] is True
