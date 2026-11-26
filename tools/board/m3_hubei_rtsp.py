# Derived from VincentttWang/NUEDC_2026 @ ed079e501579cbe891e8339b6998f3ea53a12428
# 2026H/firmware_G3507/k230/rtsp_camera_stream.py
# MIT License
#
# Copyright (c) 2026 Jianing Wang
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import _thread
import multimedia as mm
import network
import os
import time

from media.media import *
from media.sensor import *
from media.vencoder import *


RTSP_SESSION = "test"
RTSP_PORT = 8554
STREAM_WIDTH = 640
STREAM_HEIGHT = 360
STREAM_FPS = 60
STREAM_BIT_RATE = 1000
STREAM_GOP = 10
SENSOR_WIDTH = 1920
SENSOR_HEIGHT = 1080
SENSOR_FPS = 60


class CameraRtspServer:
    def __init__(self, logger=print):
        self.server = mm.rtsp_server()
        self.logger = logger
        self.streaming = False
        self.thread_over = True
        self.frame_count = 0
        self.byte_count = 0
        self.started_ms = time.ticks_ms()
        self.link = None
        self.media_ready = self.encoder_created = self.encoder_running = False
        self.rtsp_ready = self.rtsp_running = self.sensor_running = False

    def start(self):
        width = ALIGN_UP(STREAM_WIDTH, 16)
        self.sensor = Sensor(
            width=SENSOR_WIDTH,
            height=SENSOR_HEIGHT,
            fps=SENSOR_FPS,
        )
        self.sensor.reset()
        self.sensor.set_framesize(
            width=width,
            height=STREAM_HEIGHT,
            alignment=12,
        )
        self.sensor.set_pixformat(Sensor.YUV420SP)

        self.encoder = Encoder()
        self.encoder.SetOutBufs(0, 8, width, STREAM_HEIGHT)
        attributes = ChnAttrStr(
            self.encoder.PAYLOAD_TYPE_H264,
            self.encoder.H264_PROFILE_MAIN,
            width,
            STREAM_HEIGHT,
        )
        # Use a native GC2093 mode, then scale in VICAP before VENC.
        attributes.src_frame_rate = SENSOR_FPS
        attributes.dst_frame_rate = STREAM_FPS
        attributes.bit_rate = STREAM_BIT_RATE
        attributes.gop_len = STREAM_GOP
        self.link = MediaManager.link(
            self.sensor.bind_info()["src"],
            (VIDEO_ENCODE_MOD_ID, VENC_DEV_ID, 0),
        )

        MediaManager.init()
        self.media_ready = True
        self.encoder.Create(0, attributes)
        self.encoder_created = True
        self.server.rtspserver_init(RTSP_PORT)
        self.rtsp_ready = True
        self.server.rtspserver_createsession(
            RTSP_SESSION,
            mm.multi_media_type.media_h264,
            False,
        )
        self.server.rtspserver_start()
        self.rtsp_running = True
        self.encoder.Start(0)
        self.encoder_running = True
        self.sensor.run()
        self.sensor_running = True

        self.streaming = True
        self.thread_over = False
        try:
            _thread.start_new_thread(self._stream_loop, ())
        except BaseException:
            self.thread_over = True
            raise
        self.logger("HCV_CH0_CONFIG_READY")

    def _stream_loop(self):
        stream_data = StreamData()
        frame_count = 0
        try:
            while self.streaming:
                os.exitpoint()
                if self.encoder.GetStream(0, stream_data) != 0:
                    continue
                try:
                    frame_count += 1
                    self.frame_count = frame_count
                    for index in range(stream_data.pack_cnt):
                        self.byte_count += stream_data.data_size[index]
                        # The RTSP extension can consume the VENC physical buffer
                        # directly. Avoid copying every packet into a Python bytes
                        # object, which adds CPU time and garbage-collection jitter.
                        self.server.rtspserver_sendvideodata_byphyaddr(
                            RTSP_SESSION,
                            stream_data.phy_addr[index],
                            stream_data.data_size[index],
                            1000,
                        )
                finally:
                    self.encoder.ReleaseStream(0, stream_data)
                if frame_count == 1 or frame_count % 300 == 0:
                    self.logger(
                        "video frames=%d bytes=%d last_pack=%d" %
                        (frame_count, self.byte_count,
                         stream_data.data_size[0]
                         if stream_data.pack_cnt else 0))
        except BaseException as error:
            import sys
            sys.print_exception(error)
        finally:
            self.thread_over = True

    def status(self):
        elapsed = max(1, time.ticks_diff(time.ticks_ms(), self.started_ms))
        print("HCV_HUBEI_STATUS,elapsed_ms=%d,frames=%d,bytes=%d,fps=%.1f" % (
            elapsed, self.frame_count, self.byte_count, self.frame_count * 1000.0 / elapsed))

    def stop(self):
        self.streaming = False
        started = time.ticks_ms()
        while not self.thread_over and time.ticks_diff(time.ticks_ms(), started) < 2000:
            time.sleep_ms(20)
        if not self.thread_over:
            raise RuntimeError("encoder worker still active; power cycle required")
        if self.sensor_running:
            self.sensor.stop()
        if self.encoder_running:
            self.encoder.Stop(0)
        if self.encoder_created:
            self.encoder.Destroy(0)
        if self.rtsp_running:
            self.server.rtspserver_stop()
        if self.rtsp_ready:
            self.server.rtspserver_deinit()
        del self.link
        if self.media_ready:
            MediaManager.deinit()


def run(wifi):
    import sys
    sys.path.insert(0, "/sdcard/hcv")
    from hcv.guard import start, tick
    from hcv.media_session_guard import claim
    start(173)
    claim()
    ap = wifi["mode"] == "ap"
    nic = network.WLAN(network.AP_IF if ap else network.STA_IF)
    if ap:
        try:
            nic.config(ssid=wifi["ssid"], key=wifi["password"], channel=wifi["channel"])
        except TypeError:
            nic.config(ssid=wifi["ssid"], key=wifi["password"])
    else:
        nic.connect(wifi["ssid"], wifi["password"])
        began = time.ticks_ms()
        while not nic.isconnected() and time.ticks_diff(time.ticks_ms(), began) < 30000:
            tick()
            time.sleep(1)
        if not nic.isconnected():
            raise RuntimeError("STA association failed")
    address = nic.ifconfig()[0]
    if address == "0.0.0.0":
        raise RuntimeError("network address unavailable")
    print("HCV_RTSP_PRIVATE,%s,%d,%s" % (address, RTSP_PORT, RTSP_SESSION))
    if ap:
        print("HCV_AP_READY")
    server = CameraRtspServer()
    try:
        server.start()
        began = last = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), began) < 125000:
            tick()
            time.sleep(1)
            if server.thread_over:
                raise RuntimeError("encoder thread exited early")
            if time.ticks_diff(time.ticks_ms(), last) >= 5000:
                server.status()
                last = time.ticks_ms()
    finally:
        server.stop()
        print("HCV_CH0_CONFIG_CLEANUP")


if "HCV_HUBEI_WIFI" in globals():
    run(HCV_HUBEI_WIFI)
