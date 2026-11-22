"""Run a bounded, configuration-driven CHN0 hardware RTSP session."""

import sys
import time

from media.media import MediaManager
from media.sensor import Sensor

from .config import load_config
from .guard import start, tick
from .media_session_guard import claim
from .stream import H264Stream


STREAM_SECONDS = 10
GUARD_MARGIN_SECONDS = 2


def run(seconds=STREAM_SECONDS, announce_url=True, copy_packets=False):
    start(seconds + GUARD_MARGIN_SECONDS)
    config = load_config("/sdcard/hcv")
    claim()
    sensor = None
    stream = None
    media_started = False
    try:
        tick()
        mode = config["sensor"]
        sensor = Sensor(width=mode["width"], height=mode["height"], fps=mode["fps"])
        sensor.reset()
        print("HCV_CH0_CONFIG_STEP,sensor_reset")
        tick()
        stream = H264Stream(config, sensor_channel=0, copy_packets=copy_packets)
        stream.bind(sensor)
        print("HCV_CH0_CONFIG_STEP,media_link")
        tick()
        MediaManager.init()
        media_started = True
        print("HCV_CH0_CONFIG_STEP,media_init")
        tick()
        stream.start_encoder()
        print("HCV_CH0_CONFIG_STEP,encoder_started")
        tick()
        sensor.run()
        print("HCV_CH0_CONFIG_STEP,sensor_started")
        tick()
        url = stream.start_session()
        print("HCV_CH0_SEND_PATH,%s" % ("bytes" if copy_packets else "physical"))
        if announce_url:
            print("HCV_CH0_CONFIG_READY,url=%s" % url)
        else:
            print("HCV_CH0_CONFIG_READY")
        started = time.ticks_ms()
        next_status = 1000
        previous_packets = 0
        while True:
            elapsed = time.ticks_diff(time.ticks_ms(), started)
            if elapsed >= seconds * 1000:
                break
            tick()
            stream.send_one(timeout_ms=100)
            if elapsed >= next_status:
                print(
                    "HCV_CH0_CONFIG_STATUS,elapsed_ms=%d,registered_sessions=1,"
                    "packets_since_status=%d,packets=%d,send_result=%s,last_read_error=%d"
                    % (elapsed, stream.packets - previous_packets, stream.packets,
                       stream.last_send_result, stream.last_read_error)
                )
                previous_packets = stream.packets
                next_status = elapsed + 1000
        print(
            "HCV_CH0_CONFIG_SUMMARY,packets=%d,bytes=%d"
            % (stream.packets, stream.bytes)
        )
    except Exception as error:
        sys.print_exception(error)
        raise
    finally:
        try:
            if sensor is not None:
                sensor.stop()
        finally:
            try:
                if stream is not None:
                    stream.close()
            finally:
                if media_started:
                    MediaManager.deinit()
        print("HCV_CH0_CONFIG_CLEANUP")
