import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_configurable_chn0_matches_verified_probe(monkeypatch, capsys, tmp_path):
    calls = []
    firmware_attributes = []
    pack_desc = {}
    capacity = 12
    descriptors = []
    encoded = [(1, b"abc")]
    sent_addresses = []
    failed_address = None

    def operation(name, result=None):
        def record(*args, **kwargs):
            calls.append(name)
            return result
        return record

    class Sensor:
        YUV420SP = 1
        GRAYSCALE = 2
        __init__ = operation("Sensor")
        reset = operation("reset")
        set_framesize = operation("set_framesize")
        set_pixformat = operation("set_pixformat")
        bind_info = operation("bind_info", {"src": "sensor"})
        run = operation("sensor.run")
        stop = operation("sensor.stop")

        def _set_chn_fps(self, *, chn, fps):
            calls.append(("sensor.limit", chn, fps))

        def snapshot(self, *, chn, timeout):
            calls.append(("snapshot", chn))

    class Encoder:
        PAYLOAD_TYPE_H264 = 1
        H264_PROFILE_MAIN = 2
        __init__ = operation("Encoder")
        SetOutBufs = operation("SetOutBufs")
        def Create(self, channel, attributes):
            calls.append("encoder.create")
            firmware_attributes.append(vars(attributes).copy())

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

        def rtspserver_sendvideodata_byphyaddr(self, path, address, size, timestamp):
            calls.append("rtsp.send")
            sent_addresses.append(address)
            if address == failed_address:
                return 7

        rtspserver_stop = operation("rtsp.stop")
        rtspserver_deinit = operation("rtsp.deinit")

    media_manager = SimpleNamespace(
        link=link, init=operation("media.init"), deinit=operation("media.deinit")
    )

    def query_status(channel, status):
        calls.append("query_status")
        status.cur_packs = len(encoded)
        return 0

    def get_stream(channel, output, timeout):
        calls.append("get_stream")
        assert output.pack_cnt == len(encoded)
        assert timeout == 0
        for index, (kind, payload) in enumerate(encoded):
            pack = descriptors[-capacity + index]
            pack.type, pack.len, pack.phys_addr, pack.pts = kind, len(payload), index, 1000
        return 0

    def struct(address, desc, mode):
        value = SimpleNamespace()
        if desc is pack_desc:
            descriptors.append(value)
        return value

    venc = SimpleNamespace(
        kd_mpi_venc_create_chn=operation("encoder.create", 0),
        kd_mpi_venc_query_status=query_status,
        kd_mpi_venc_get_stream=get_stream,
        kd_mpi_venc_release_stream=operation("release_stream", 0),
    )
    modules = {
        "media.media": SimpleNamespace(MediaManager=media_manager, VENC_DEV_ID=0,
                                       VIDEO_ENCODE_MOD_ID=0, VENC_PACK_CNT_MAX=capacity),
        "media.sensor": SimpleNamespace(Sensor=Sensor),
        "media.vencoder": SimpleNamespace(
            Encoder=Encoder,
            ChnAttrStr=lambda *args: SimpleNamespace(),
            StreamData=lambda: SimpleNamespace(pack_cnt=1, data=[0], data_size=[3]),
        ),
        "multimedia": SimpleNamespace(
            rtsp_server=Rtsp, multi_media_type=SimpleNamespace(media_h264=1)
        ),
        "mpp": SimpleNamespace(
            venc=venc,
            sys=SimpleNamespace(kd_mpi_sys_mmap=lambda phys, size: (calls.append("mmap"), phys + 1)[1],
                                kd_mpi_sys_munmap=operation("munmap", 0)),
            venc_def=SimpleNamespace(k_venc_pack_desc=pack_desc, k_venc_stream_desc={}),
            venc_struct=SimpleNamespace(
                K_VENC_RC_MODE_CBR=1, K_VENC_P_FRAME=1, K_VENC_I_FRAME=2, K_VENC_HEADER=3,
                k_venc_chn_status=SimpleNamespace,
                k_venc_chn_attr=lambda: SimpleNamespace(
                    venc_attr=SimpleNamespace(), rc_attr=SimpleNamespace(cbr=SimpleNamespace())
                ),
            ),
        ),
        "uctypes": SimpleNamespace(
            NATIVE=0,
            sizeof=lambda *args: 8,
            addressof=id,
            struct=struct,
            bytearray_at=lambda addr, size: encoded[addr - 1][1] if addr else b"abc",
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

    stream_module = load("stream")
    stream_module.time = SimpleNamespace(ticks_ms=lambda: 0, ticks_add=lambda a, b: a + b)
    monkeypatch.setitem(sys.modules, "k230.hcv.stream", stream_module)
    baseline = load("ch0_rtsp_probe")
    configurable = load("ch0_configurable_probe")
    config = {
        "sensor": {"width": 1280, "height": 720, "fps": 90},
        "chn_detect": {"width": 640, "height": 360, "crop": None},
        "chn_stream": {"width": 960, "height": 540},
        "stream": {"port": 8554, "path": "hcv", "fps": 30, "bitrate_kbps": 2000, "gop": 30},
    }
    sequences = []
    for probe, options in ((baseline, {}), (configurable, {}), (configurable, {"copy_packets": True}),
                           (configurable, {"use_firmware_encoder": True})):
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
    query = expected.index("get_stream")
    configurable_expected = expected[:query] + ["query_status"] + expected[query:]
    assert sequences == [
        expected, configurable_expected,
        configurable_expected[:send + 1] + ["mmap", "munmap"] + configurable_expected[send + 1:],
        configurable_expected,
    ]
    assert firmware_attributes[-1] == dict(src_frame_rate=90, dst_frame_rate=30,
                                           bit_rate=2000, gop_len=30)

    encoded[:] = [(3, b"\x00\x00\x00\x01\x67sps"), (2, b"\x00\x00\x01\x65idr"), (1, b"p")]
    sent_addresses.clear()
    ticks = iter((0, 1001, 1005, 10_003))
    configurable.run(vicap_stream_limit=True, use_firmware_encoder=True)
    assert calls.count(("sensor.limit", 0, 30)) == 1
    assert calls.index(("sensor.limit", 0, 30)) < calls.index("sensor.run")
    assert calls.index("sensor.run") < calls.index(("snapshot", 1)) < calls.index("get_stream")
    assert firmware_attributes[-1]["src_frame_rate"] == 30
    assert firmware_attributes[-1]["dst_frame_rate"] == 30
    assert sent_addresses == [0, 1, 2]
    assert calls.count("release_stream") == 1
    assert calls.index("release_stream") > max(i for i, name in enumerate(calls) if name == "rtsp.send")
    output = capsys.readouterr().out
    assert output.index("HCV_CH0_CONFIG_READY") < output.index("HCV_RTSP_FIRST_IDR,elapsed_ms=1005,pts_ms=1")
    assert "HCV_VENC_PACKS,1,3,3,1,1,1,1" in output

    clip = configurable.RawClip(config["stream"]["bitrate_kbps"])
    stream = stream_module.H264Stream(config, trace_frames=True)
    stream.start_session()
    calls.clear()
    sent_addresses.clear()
    assert stream.send_one(timeout_ms=0, capture=clip)
    assert sent_addresses == [0, 1, 2]
    target = tmp_path / "clip.h264"
    clip.save(str(target), completed=False)
    assert not target.exists()
    write_ticks = iter((4142, 4159))
    monkeypatch.setattr(configurable.time, "ticks_ms", lambda: next(write_ticks))
    clip.save(str(target), completed=True)
    assert target.read_bytes() == b"".join(payload for _, payload in encoded)
    assert capsys.readouterr().out.splitlines()[-1].endswith(",17")

    # The clip must survive a later firmware hang, before sensor.stop or RTSP teardown.
    ticks = iter((0, 25000, 25001, 35000, 35000, 35000, 35017, 35022, 60000))
    configurable.time = SimpleNamespace(ticks_ms=lambda: next(ticks), ticks_diff=lambda a, b: a - b)
    saved = tmp_path / "early.h264"
    def stop_with_error(*_):
        assert saved.read_bytes() == b"".join(payload for _, payload in encoded)
        raise RuntimeError("sensor stop failed")

    with monkeypatch.context() as patch:
        patch.setattr(Sensor, "stop", stop_with_error)
        with pytest.raises(RuntimeError, match="sensor stop failed"):
            configurable.run(seconds=60, vicap_stream_limit=True, raw_clip_path=str(saved))
    log = capsys.readouterr().out
    assert log.count("HCV_RAW_CLIP_SAVED,") == 1
    assert "pause_ms=22" in log

    stream = stream_module.H264Stream(config, trace_frames=True)
    stream.start_session()
    calls.clear()
    sent_addresses.clear()
    failed_address = 1
    with pytest.raises(OSError, match="send H.264 packet returned 7"):
        stream.send_one(timeout_ms=0)
    assert sent_addresses == [0, 1]
    assert calls[-1] == "release_stream" and calls.count("release_stream") == 1
    assert stream.first_idr_pts_ms is None

    calls.clear()
    encoded.clear()
    assert not stream.send_one(timeout_ms=0)
    assert calls == ["query_status"]
