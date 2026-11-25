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


def run(seconds=STREAM_SECONDS, announce_url=True, copy_packets=False, config=None,
        use_firmware_encoder=False, vicap_stream_limit=False):
    start(seconds + GUARD_MARGIN_SECONDS)
    if config is None:
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
        if vicap_stream_limit:
            detector = config["chn_detect"]
            sensor.set_framesize(width=detector["width"], height=detector["height"],
                                 crop=detector["crop"], chn=1)
            sensor.set_pixformat(Sensor.GRAYSCALE, chn=1)
        stream = H264Stream(config, sensor_channel=0, copy_packets=copy_packets,
                            trace_frames=vicap_stream_limit)
        stream.bind(sensor, source_fps=config["stream"]["fps"] if vicap_stream_limit else None)
        if vicap_stream_limit:
            print("HCV_VICAP_LIMIT,stream_chn=0,fps=%d,detector_sample_chn=1" % stream.source_fps)
        print("HCV_CH0_CONFIG_STEP,media_link")
        tick()
        MediaManager.init()
        media_started = True
        print("HCV_CH0_CONFIG_STEP,media_init")
        tick()
        stream.start_encoder(use_firmware_encoder=use_firmware_encoder)
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
        previous_packets = previous_bytes = previous_elapsed = 0
        detector_frames = detector_errors = 0
        first_idr_notified = False
        while True:
            elapsed = time.ticks_diff(time.ticks_ms(), started)
            if elapsed >= seconds * 1000:
                break
            tick()
            if vicap_stream_limit:
                # Blocking for each encoded frame would pace this sampler at the stream FPS.
                try:
                    sensor.snapshot(chn=1, timeout=25)
                    detector_frames += 1
                except RuntimeError:
                    detector_errors += 1
                stream.send_one(timeout_ms=0)
                if not first_idr_notified and stream.first_idr_pts_ms is not None:
                    print("HCV_RTSP_FIRST_IDR,elapsed_ms=%d,pts_ms=%d" % (
                        time.ticks_diff(time.ticks_ms(), started), stream.first_idr_pts_ms,
                    ))
                    first_idr_notified = True
            else:
                stream.send_one(timeout_ms=100)
            if elapsed >= next_status:
                print(
                    "HCV_CH0_CONFIG_STATUS,elapsed_ms=%d,registered_sessions=1,"
                    "packets_since_status=%d,packets=%d,send_result=%s,last_read_error=%d,"
                    "bytes_since_status=%d,bytes=%d,interval_ms=%d,last_packet_pts_ms=%d"
                    % (elapsed, stream.packets - previous_packets, stream.packets,
                       stream.last_send_result, stream.last_read_error,
                       stream.bytes - previous_bytes, stream.bytes, elapsed - previous_elapsed,
                       stream.last_packet_pts_ms)
                )
                if vicap_stream_limit:
                    print("HCV_VICAP_DETECT,elapsed_ms=%d,frames=%d,errors=%d" % (
                        elapsed, detector_frames, detector_errors,
                    ))
                previous_packets = stream.packets
                previous_bytes = stream.bytes
                previous_elapsed = elapsed
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
