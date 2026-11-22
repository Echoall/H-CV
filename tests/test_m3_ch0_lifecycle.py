import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

from tools.analysis.m3_sta_record import redact


def test_sta_log_redacts_credentials_without_losing_traceback():
    wifi = {"ssid": "test-hotspot", "password": "dummy-test-password"}
    raw = (
        'Traceback (most recent call last):\n'
        '  File "probe.py", line 60, in run\n'
        'OSError: test-hotspot dummy-test-password rtsp://192.0.2.1:8554/hcv\n'
        'Session: ABC123;timeout=10'
    )
    assert redact(raw, wifi) == (
        'Traceback (most recent call last):\n'
        '  File "probe.py", line 60, in run\n'
        'OSError: <redacted> <redacted> rtsp://<ipv4>:8554/hcv\n'
        'Session: <session>;timeout=10'
    )


def test_configurable_chn0_matches_verified_probe(monkeypatch):
    calls = []

    def operation(name, result=None):
        def record(*args, **kwargs):
            calls.append(name)
            return result
        return record

    class Sensor:
        YUV420SP = 1
        __init__ = operation("Sensor")
        reset = operation("reset")
        set_framesize = operation("set_framesize")
        set_pixformat = operation("set_pixformat")
        bind_info = operation("bind_info", {"src": "sensor"})
        run = operation("sensor.run")
        stop = operation("sensor.stop")

    class Encoder:
        PAYLOAD_TYPE_H264 = 1
        H264_PROFILE_MAIN = 2
        __init__ = operation("Encoder")
        SetOutBufs = operation("SetOutBufs")
        Create = operation("encoder.create")
        Start = operation("encoder.start")
        GetStream = operation("get_stream")
        ReleaseStream = operation("release_stream")
        Stop = operation("encoder.stop")
        Destroy = operation("encoder.destroy")

    class Link:
        __del__ = operation("release_link")

    def link(*args):
        calls.append("link")
        return Link()

    class Rtsp:
        __init__ = operation("RTSP")
        rtspserver_init = operation("rtsp.init")
        rtspserver_createsession = operation("rtsp.create_session")
        rtspserver_start = operation("rtsp.start")
        rtspserver_getrtspurl = operation("rtsp.get_url", "rtsp://test/hcv")
        def rtspserver_sendvideodata(self, path, payload, size, timestamp):
            assert payload == b"abc" and size == 3
            calls.append("rtsp.send")

        rtspserver_sendvideodata_byphyaddr = operation("rtsp.send")
        rtspserver_stop = operation("rtsp.stop")
        rtspserver_deinit = operation("rtsp.deinit")

    media_manager = SimpleNamespace(
        link=link, init=operation("media.init"), deinit=operation("media.deinit")
    )
    venc = SimpleNamespace(
        kd_mpi_venc_create_chn=operation("encoder.create", 0),
        kd_mpi_venc_get_stream=operation("get_stream", 0),
        kd_mpi_venc_release_stream=operation("release_stream", 0),
    )
    modules = {
        "media.media": SimpleNamespace(MediaManager=media_manager, VENC_DEV_ID=0, VIDEO_ENCODE_MOD_ID=0),
        "media.sensor": SimpleNamespace(Sensor=Sensor),
        "media.vencoder": SimpleNamespace(
            Encoder=Encoder,
            ChnAttrStr=lambda *args: object(),
            StreamData=lambda: SimpleNamespace(pack_cnt=1, data=[0], data_size=[3]),
        ),
        "multimedia": SimpleNamespace(
            rtsp_server=Rtsp, multi_media_type=SimpleNamespace(media_h264=1)
        ),
        "mpp": SimpleNamespace(
            venc=venc,
            sys=SimpleNamespace(kd_mpi_sys_mmap=operation("mmap", 123),
                                kd_mpi_sys_munmap=operation("munmap", 0)),
            venc_def=SimpleNamespace(k_venc_pack_desc={}, k_venc_stream_desc={}),
            venc_struct=SimpleNamespace(
                K_VENC_RC_MODE_CBR=1,
                k_venc_chn_attr=lambda: SimpleNamespace(
                    venc_attr=SimpleNamespace(), rc_attr=SimpleNamespace(cbr=SimpleNamespace())
                ),
            ),
        ),
        "uctypes": SimpleNamespace(
            NATIVE=0,
            sizeof=lambda *args: 8,
            addressof=id,
            struct=lambda *args: SimpleNamespace(len=3, phys_addr=0, pts=1000),
            bytearray_at=lambda *args: b"abc",
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def load(name):
        path = Path(__file__).resolve().parents[1] / "k230" / "hcv" / (name + ".py")
        spec = spec_from_file_location("k230.hcv." + name, path)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    monkeypatch.setitem(sys.modules, "k230.hcv.stream", load("stream"))
    baseline = load("ch0_rtsp_probe")
    configurable = load("ch0_configurable_probe")
    config = {
        "sensor": {"width": 1280, "height": 720, "fps": 90},
        "chn_stream": {"width": 960, "height": 540},
        "stream": {"port": 8554, "path": "hcv", "fps": 30, "bitrate_kbps": 2000, "gop": 30},
    }
    sequences = []
    for probe, options in ((baseline, {}), (configurable, {}), (configurable, {"copy_packets": True})):
        ticks = iter((0, 1, 10_000))
        probe.time = SimpleNamespace(ticks_ms=lambda: next(ticks), ticks_diff=lambda a, b: a - b)
        probe.load_config = lambda path: config
        probe.start = lambda seconds: None
        probe.claim = lambda: None
        probe.tick = lambda: None
        probe.run(**options)
        sequences.append(calls[:])
        calls.clear()

    expected = [
        "Sensor", "reset", "set_framesize", "set_pixformat", "Encoder", "SetOutBufs",
        "bind_info", "link", "media.init", "encoder.create", "encoder.start", "sensor.run",
        "RTSP", "rtsp.init", "rtsp.create_session", "rtsp.start", "rtsp.get_url",
        "get_stream", "rtsp.send", "release_stream", "sensor.stop", "rtsp.stop",
        "rtsp.deinit", "release_link", "encoder.stop", "encoder.destroy", "media.deinit",
    ]
    send = expected.index("rtsp.send")
    assert sequences == [expected, expected, expected[:send] + ["mmap", "munmap"] + expected[send:]]
