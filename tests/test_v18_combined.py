import runpy
import sys
import types

import pytest

from station.bench import bench, hubei_m1


@pytest.mark.parametrize("v18", [True, False])
def test_combined_session_configures_channels_and_orders_encoder_creation(monkeypatch, v18):
    calls = []

    class Device:
        YUV420SP = PAYLOAD_TYPE_H264 = H264_PROFILE_MAIN = 1

        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            def record(*args, **kwargs):
                calls.append(name)
                if name == "bind_info":
                    return {"src": (0, 0, 0)}
            return record

    def module(name, **attrs):
        mod = types.ModuleType(name)
        vars(mod).update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)

    module("media")
    module("network")
    module("multimedia", rtsp_server=Device, multi_media_type=types.SimpleNamespace(media_h264=1))
    module("media.sensor", Sensor=Device)
    module("media.vencoder", Encoder=Device, ChnAttrStr=Device, VIDEO_ENCODE_MOD_ID=1, VENC_DEV_ID=0)
    module("media.media", MediaManager=Device(), ALIGN_UP=lambda value, alignment: value)
    import _thread
    import time
    monkeypatch.setattr(_thread, "start_new_thread", lambda fn, args: calls.append("worker"))
    monkeypatch.setattr(time, "ticks_ms", lambda: 0, raising=False)
    ns = runpy.run_path(str(bench.ROOT / "tools/board/m3_hubei_rtsp.py"))
    instance = ns["CameraRtspServer"](logger=calls.append)
    instance.start(types.SimpleNamespace(configure=lambda sensor: calls.append("CHN1")), v18)
    assert calls.index("set_pixformat") < calls.index("CHN1") < calls.index("run")
    assert calls.index("SetOutBufs") < calls.index("Create") < calls.index("Start")
    assert (calls.index("Create") < calls.index("link")) == v18
    assert ("init" not in calls) == v18
    assert calls.index("run") < calls.index("worker")


def test_v18_combined_deploys_version_checked_dependencies_and_baseline_config(tmp_path):
    run = bench.Run(tmp_path / "combined", {"id": "test"}, True)
    targets = []

    def upload(stage, cmd, **kwargs):
        assert cmd[-1] == "--hiwonder-v18"
        targets.append(cmd[-2])
        return 0, "", ""

    run.command = upload
    entry = hubei_m1.prepare(run, v18=True)
    assert "/sdcard/hcv/hubei_m1_runner.py" in targets
    assert "/sdcard/hcv/hcv/hubei_detection.py" in targets
    assert "/sdcard/hcv/m1_v18/config.json" in targets
    assert "load_config('/sdcard/hcv/m1_v18')" in entry
