import ast
import json
from types import SimpleNamespace

import pytest

from station.bench import hubei
from station.bench.render_pipe import RenderedFrames


def test_probe_does_not_construct_media_or_call_mpi():
    path = hubei.bench.ROOT / "tools/board/m3_hubei_inventory.py"
    calls = {ast.unparse(n.func) for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
             if isinstance(n, ast.Call)}
    assert calls <= {"ALIGN_UP", "hasattr", "os.uname", "print", "int"}


@pytest.mark.parametrize("sta", [None, {"ssid": "test", "password": ""}, {"ssid": "test", "password": "12345678"}])
def test_sta_preflight_is_read_only(tmp_path, monkeypatch, sta):
    monkeypatch.setattr(hubei.bench, "HERE", tmp_path)
    config = tmp_path / "config.local.json"
    config.write_text(json.dumps({"hubei_sta": sta}), encoding="utf-8")
    before = config.read_bytes()
    assert bool(hubei.saved_sta()) == bool(sta and sta["password"])
    assert config.read_bytes() == before


def test_repeated_vlc_frames_are_not_display_progress():
    renderer = RenderedFrames(2, 2)
    renderer.feed(b"I420 W2 H2 F60:1 Ip A1:1\nFRA")
    renderer.feed(b"ME\nabcdef")
    first = renderer.picture
    assert first is not None
    renderer.feed(b"FRAME\nabcdefFRAME\na")
    assert renderer.picture == first
    renderer.feed(b"bcdeX")
    assert renderer.picture != first


@pytest.mark.parametrize("display,duration,gap,result", [
    (60, 58, 1, "通过"), (59.99, 62, 0, "失败"), (62, 57.99, 0, "失败"),
    (62, 62, 1.01, "失败"), (62, 62, None, "失败")])
def test_recording_acceptance(tmp_path, monkeypatch, display, duration, gap, result):
    run = hubei.bench.Run(tmp_path / "ap", hubei.plan("ap"), False)
    run.summary.update(rtsp_ready_at="start")
    run.summary["capture"].update(transport_observed=["tcp"], continuous_display_s=display, frames=3000,
                                  video_track_seconds=duration, width=640, height=360)
    (run.directory / "recording.mp4").touch()
    (run.directory / "latency-01.png").touch()
    monkeypatch.setattr(hubei, "frame_numbers", lambda *_: {"gap_pct": gap})
    assert hubei.assess(run, True, "ffmpeg") == result
    run.summary.pop("rtsp_ready_at")
    assert hubei.assess(run, False, "ffmpeg") == "未验证"


def test_missing_usb_is_inconclusive_and_does_not_trigger_sta(tmp_path, monkeypatch):
    monkeypatch.setattr(hubei.bench, "tools", lambda: ("vlc", None, None))
    monkeypatch.setattr(hubei.bench, "find_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr(hubei.shutil, "which", lambda _: __file__)
    monkeypatch.setattr(hubei.wlan, "snapshot", lambda: ("wifi", "original", "profile"))
    monkeypatch.setattr(hubei.wlan, "restore", lambda *a, **kw: True)
    monkeypatch.setattr(hubei.bench, "config_wifi", lambda: {"ssid": "board", "password": "dummy"})
    monkeypatch.setattr(hubei.list_ports, "comports", lambda: [])
    assert hubei.wizard(SimpleNamespace(simulate=None, runs=tmp_path)) == 1
    report = json.loads(next(tmp_path.glob("*/summary.json")).read_text(encoding="utf-8"))
    assert [(row["mode"], row["result"]) for row in report["rounds"]] == [("ap", "未验证")]
