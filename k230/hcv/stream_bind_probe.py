"""Probe the camera-to-encoder setup one operation at a time."""

from media.media import MediaManager, VENC_DEV_ID, VIDEO_ENCODE_MOD_ID
from media.sensor import Sensor
from media.vencoder import Encoder
from mpp import venc, venc_def, venc_struct

from .config import load_config
from .guard import start, tick
from main_hcv import configure_detector_sensor


CHANNEL = 0
STREAM_BUFFERS = 8


def checked(result, action):
    if result not in (None, 0):
        raise OSError("%s returned %s" % (action, result))


def release_link(link):
    if link is None:
        return
    del link


def encoder_attributes(config):
    width = config["chn_stream"]["width"]
    height = config["chn_stream"]["height"]
    attrs = venc_struct.k_venc_chn_attr()
    attrs.venc_attr.type = Encoder.PAYLOAD_TYPE_H264
    attrs.venc_attr.stream_buf_size = (width * height * 3 // 4 + 4095) & ~4095
    attrs.venc_attr.stream_buf_cnt = STREAM_BUFFERS
    attrs.venc_attr.pic_width = width
    attrs.venc_attr.pic_height = height
    attrs.venc_attr.profile = Encoder.H264_PROFILE_MAIN
    attrs.rc_attr.rc_mode = venc_struct.K_VENC_RC_MODE_CBR
    attrs.rc_attr.cbr.gop = config["stream"]["gop"]
    attrs.rc_attr.cbr.src_frame_rate = config["sensor"]["fps"]
    attrs.rc_attr.cbr.dst_frame_rate = config["stream"]["fps"]
    attrs.rc_attr.cbr.bit_rate = config["stream"]["bitrate_kbps"]
    return attrs


def run(stage):
    stages = (
        "chn1",
        "setbufs",
        "create_before_init",
        "link_before_init",
        "create_link_init",
        "link_init_create",
        "init_create_link",
        "create_after_init",
        "link_after_init",
    )
    if stage not in stages:
        raise ValueError("unknown bind probe stage: %s" % stage)

    start(5)
    config = load_config("/sdcard/hcv")
    sensor = None
    encoder = None
    link = None
    created = False
    media_deinit_needed = stage not in ("chn1", "setbufs")
    sensor_started = False
    try:
        tick()
        sensor = Sensor(
            width=config["sensor"]["width"],
            height=config["sensor"]["height"],
            fps=config["sensor"]["fps"],
        )
        configure_detector_sensor(sensor, config)
        channel = config["chn_stream"]
        sensor.set_framesize(
            width=channel["width"], height=channel["height"], chn=1
        )
        sensor.set_pixformat(Sensor.YUV420SP, chn=1)
        print("HCV_BIND_STEP,chn1_configured")
        tick()
        if stage == "chn1":
            return

        encoder = Encoder()
        encoder.SetOutBufs(
            CHANNEL,
            STREAM_BUFFERS,
            channel["width"],
            channel["height"],
        )
        print("HCV_BIND_STEP,set_out_bufs")
        tick()
        if stage == "setbufs":
            return

        if stage in ("create_after_init", "link_after_init"):
            MediaManager.init()
            print("HCV_BIND_STEP,media_init")
            tick()

        if stage in (
            "create_before_init",
            "create_after_init",
            "link_before_init",
            "link_after_init",
            "create_link_init",
            "link_init_create",
            "init_create_link",
        ):
            if stage == "link_init_create":
                source = sensor.bind_info(chn=1)["src"]
                print("HCV_BIND_STEP,bind_info")
                tick()
                link = MediaManager.link(
                    source,
                    (VIDEO_ENCODE_MOD_ID, VENC_DEV_ID, CHANNEL),
                )
                print("HCV_BIND_STEP,media_link")
                tick()
                MediaManager.init()
                media_started = True
                print("HCV_BIND_STEP,media_init")
                tick()
            checked(
                venc.kd_mpi_venc_create_chn(CHANNEL, encoder_attributes(config)),
                "create H.264 encoder",
            )
            created = True
            print("HCV_BIND_STEP,encoder_created")
            tick()

        if stage in ("link_before_init", "link_after_init", "create_link_init", "init_create_link"):
            if stage == "init_create_link":
                source = sensor.bind_info(chn=1)["src"]
                print("HCV_BIND_STEP,bind_info")
                tick()
                link = MediaManager.link(
                    source,
                    (VIDEO_ENCODE_MOD_ID, VENC_DEV_ID, CHANNEL),
                )
                print("HCV_BIND_STEP,media_link")
                tick()
            elif stage == "create_link_init":
                source = sensor.bind_info(chn=1)["src"]
                print("HCV_BIND_STEP,bind_info")
                tick()
                link = MediaManager.link(
                    source,
                    (VIDEO_ENCODE_MOD_ID, VENC_DEV_ID, CHANNEL),
                )
                print("HCV_BIND_STEP,media_link")
                tick()
                MediaManager.init()
                media_started = True
                print("HCV_BIND_STEP,media_init")
                tick()
            elif stage == "link_before_init":
                source = sensor.bind_info(chn=1)["src"]
                print("HCV_BIND_STEP,bind_info")
                tick()
                link = MediaManager.link(
                    source,
                    (VIDEO_ENCODE_MOD_ID, VENC_DEV_ID, CHANNEL),
                )
                print("HCV_BIND_STEP,media_link")
                tick()
            elif stage == "link_after_init":
                source = sensor.bind_info(chn=1)["src"]
                print("HCV_BIND_STEP,bind_info")
                tick()
                link = MediaManager.link(
                    source,
                    (VIDEO_ENCODE_MOD_ID, VENC_DEV_ID, CHANNEL),
                )
                print("HCV_BIND_STEP,media_link")
                tick()
    finally:
        try:
            if sensor is not None:
                sensor_ref = sensor
                sensor = None
                try:
                    sensor_ref.stop()
                finally:
                    del sensor_ref
                print("HCV_BIND_CLEANUP,sensor_stop")
        finally:
            try:
                if hasattr(Sensor, "deinit"):
                    Sensor.deinit()
                    print("HCV_BIND_CLEANUP,sensor_deinit")
            finally:
                try:
                    if link is not None:
                        release_link(link)
                        link = None
                finally:
                    try:
                        if created:
                            encoder.Destroy(CHANNEL)
                    finally:
                        try:
                            if media_deinit_needed:
                                MediaManager.deinit()
                        finally:
                            print("HCV_BIND_CLEANUP,stage=%s" % stage)
