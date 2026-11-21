"""Run the CanMV v1.4 official CHN0 encoder and RTSP lifecycle briefly."""

import os
import time
import uctypes

import multimedia as mm
from media.media import (
    MediaManager,
    VENC_DEV_ID,
    VIDEO_ENCODE_MOD_ID,
)
from media.sensor import Sensor
from media.vencoder import Encoder, ChnAttrStr, StreamData

from .config import load_config
from .guard import start, tick


VENC_CHANNEL = 0
STREAM_NAME = "hcv"
STREAM_SECONDS = 3


def checked(result, action):
    if result not in (None, 0):
        raise OSError("%s returned %s" % (action, result))


def release_link(link):
    if link is None:
        return
    unlink = getattr(MediaManager, "_del_link", None)
    if unlink is not None:
        unlink(link)
    else:
        del link


def run():
    start(5)
    config = load_config("/sdcard/hcv")
    width = 1280
    height = 720
    sensor = None
    encoder = None
    rtsp = None
    link = None
    media_started = False
    encoder_started = False
    rtsp_started = False
    try:
        tick()
        sensor = Sensor(
            width=config["sensor"]["width"],
            height=config["sensor"]["height"],
            fps=config["sensor"]["fps"],
        )
        sensor.reset()
        sensor.set_framesize(width=width, height=height, alignment=12)
        sensor.set_pixformat(Sensor.YUV420SP)
        print("HCV_CH0_STEP,sensor_configured")
        tick()

        encoder = Encoder()
        encoder.SetOutBufs(VENC_CHANNEL, 8, width, height)
        print("HCV_CH0_STEP,set_out_bufs")
        tick()

        link = MediaManager.link(
            sensor.bind_info()["src"],
            (VIDEO_ENCODE_MOD_ID, VENC_DEV_ID, VENC_CHANNEL),
        )
        print("HCV_CH0_STEP,media_link")
        tick()
        MediaManager.init()
        media_started = True
        print("HCV_CH0_STEP,media_init")
        tick()

        attrs = ChnAttrStr(
            encoder.PAYLOAD_TYPE_H264,
            encoder.H264_PROFILE_MAIN,
            width,
            height,
        )
        encoder.Create(VENC_CHANNEL, attrs)
        print("HCV_CH0_STEP,encoder_created")
        tick()
        encoder.Start(VENC_CHANNEL)
        encoder_started = True
        print("HCV_CH0_STEP,encoder_started")
        sensor.run()
        print("HCV_CH0_STEP,sensor_started")
        tick()

        rtsp = mm.rtsp_server()
        checked(rtsp.rtspserver_init(config["stream"]["port"]), "start RTSP listener")
        checked(
            rtsp.rtspserver_createsession(
                STREAM_NAME,
                mm.multi_media_type.media_h264,
                False,
            ),
            "create RTSP session",
        )
        rtsp.rtspserver_start()
        rtsp_started = True
        print(
            "HCV_CH0_READY,url=%s"
            % rtsp.rtspserver_getrtspurl(STREAM_NAME)
        )

        stream_data = StreamData()
        started = time.ticks_ms()
        packets = 0
        bytes_sent = 0
        while time.ticks_diff(time.ticks_ms(), started) < STREAM_SECONDS * 1000:
            tick()
            encoder.GetStream(VENC_CHANNEL, stream_data)
            try:
                for index in range(stream_data.pack_cnt):
                    payload = bytes(
                        uctypes.bytearray_at(
                            stream_data.data[index],
                            stream_data.data_size[index],
                        )
                    )
                    checked(
                        rtsp.rtspserver_sendvideodata(
                            STREAM_NAME,
                            payload,
                            stream_data.data_size[index],
                            1000,
                        ),
                        "send RTSP packet",
                    )
                    packets += 1
                    bytes_sent += stream_data.data_size[index]
            finally:
                encoder.ReleaseStream(VENC_CHANNEL, stream_data)
        print(
            "HCV_CH0_SUMMARY,packets=%d,bytes=%d"
            % (packets, bytes_sent)
        )
    finally:
        try:
            if sensor is not None:
                sensor.stop()
        finally:
            try:
                if rtsp_started:
                    rtsp.rtspserver_stop()
            finally:
                try:
                    if rtsp is not None:
                        rtsp.rtspserver_deinit()
                finally:
                    try:
                        if link is not None:
                            release_link(link)
                            link = None
                    finally:
                        try:
                            if encoder_started:
                                encoder.Stop(VENC_CHANNEL)
                        finally:
                            try:
                                if encoder is not None:
                                    encoder.Destroy(VENC_CHANNEL)
                            finally:
                                if media_started:
                                    MediaManager.deinit()
        print("HCV_CH0_CLEANUP")
