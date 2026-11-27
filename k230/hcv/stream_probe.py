"""Bounded camera, hardware encoder and RTSP diagnostic stages."""

import time

from media.media import MediaManager
from media.sensor import Sensor

from .config import load_config
from .guard import start, tick
from .media_session_guard import claim
from main_hcv import configure_detector_sensor


def run(stage, use_ap=False):
    if stage not in ("camera", "encode", "rtsp"):
        raise ValueError("stage must be camera, encode or rtsp")
    start(5)
    config = load_config("/sdcard/hcv")
    claim()
    mode = config["sensor"]
    sensor = None
    stream = None
    media_started = False
    ap = None
    try:
        if use_ap:
            import network
            wifi = config["wifi"]
            password = wifi["password"]
            if wifi["mode"] != "ap" or password == "SEE_LOCAL" or len(password) < 8:
                raise ValueError("set the private AP configuration before running the RTSP AP probe")
            ap = network.WLAN(network.AP_IF)
            print("HCV_RTSP_STEP,ap_config")
            ap.config(ssid=wifi["ssid"], key=password, channel=wifi["channel"])
            print("HCV_RTSP_AP,ip=%s" % ap.ifconfig()[0])
        tick()
        print("HCV_RTSP_STEP,sensor_construct")
        sensor = Sensor(width=mode["width"], height=mode["height"], fps=mode["fps"])
        print("HCV_RTSP_STEP,configure_chn0")
        configure_detector_sensor(sensor, config)
        tick()
        if stage == "camera":
            print("HCV_RTSP_STEP,configure_chn1")
            channel = config["chn_stream"]
            sensor.set_framesize(width=channel["width"], height=channel["height"], chn=1)
            sensor.set_pixformat(Sensor.YUV420SP, chn=1)
        else:
            from .stream import H264Stream
            print("HCV_RTSP_STEP,allocate_encoder_descriptors")
            stream = H264Stream(config)
            print("HCV_RTSP_STEP,configure_and_bind_chn1")
            stream.bind(sensor)
        tick()
        print("HCV_RTSP_STEP,media_init")
        media_started = True
        MediaManager.init()
        if stream is not None:
            tick()
            print("HCV_RTSP_STEP,encoder_start")
            stream.start_encoder()
        if stage == "rtsp":
            tick()
            print("HCV_RTSP_STEP,rtsp_start")
            url = stream.start_session()
            print("HCV_RTSP_READY,url=%s" % url)
        print("HCV_RTSP_STEP,sensor_start")
        tick()
        sensor.run()
        if stream is None:
            tick()
            sensor.snapshot(chn=0, timeout=1000)
            tick()
            sensor.snapshot(chn=1, timeout=1000)
            print("HCV_RTSP_CAMERA,chn0=1,chn1=1")
            return
        started = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), started) < 1000:
            tick()
            stream.send_one(timeout_ms=100)
        print("HCV_RTSP_SUMMARY,packets=%d,bytes=%d,elapsed_ms=%d,last_read_error=%d" % (
            stream.packets, stream.bytes, time.ticks_diff(time.ticks_ms(), started),
            stream.last_read_error,
        ))
        if not stream.packets:
            raise RuntimeError("hardware encoder did not produce packets")
    finally:
        try:
            if sensor is not None:
                sensor.stop()
        finally:
            try:
                if stream is not None:
                    stream.close()
            finally:
                try:
                    if media_started:
                        MediaManager.deinit()
                finally:
                    if ap is not None:
                        ap.stop()
        print("HCV_RTSP_CLEANUP,stage=%s" % stage)
