"""Finite CanMV capture helper for P1-T01."""

import gc
import json
import os
import time

from hcv.guard import start as guard_start
from hcv.guard import tick as guard_tick
from media.media import MediaManager
from media.sensor import Sensor


SENSOR_WIDTH = 1280
SENSOR_HEIGHT = 720
SENSOR_FPS = 90
DETECT_WIDTH = 640
DETECT_HEIGHT = 360
MAX_SEQUENCE_FRAMES = 300


def _ticks_us():
    return time.ticks_us()


def _save_image(image, path, pixel_format):
    if pixel_format == "GRAYSCALE":
        raw_path = path.rsplit(".", 1)[0] + ".gray.bin"
        with open(raw_path, "wb") as stream:
            stream.write(image.to_numpy_ref().tobytes())
        return raw_path
    raw_path = path.rsplit(".", 1)[0] + ".yuv420sp"
    image.save(raw_path)
    return raw_path


def _make_sensor(capture_chn1):
    sensor = Sensor(width=SENSOR_WIDTH, height=SENSOR_HEIGHT, fps=SENSOR_FPS)
    sensor.reset()
    sensor.set_framesize(width=DETECT_WIDTH, height=DETECT_HEIGHT, chn=0)
    sensor.set_pixformat(Sensor.GRAYSCALE, chn=0)
    if capture_chn1:
        sensor.set_framesize(width=SENSOR_WIDTH, height=SENSOR_HEIGHT, chn=1)
        sensor.set_pixformat(Sensor.YUV420SP, chn=1)
    MediaManager.init()
    sensor.run()
    for _ in range(8):
        guard_tick()
        sensor.snapshot(chn=0, timeout=1000)
    return sensor


def _snapshot_pair(sensor, capture_chn1):
    captured = _ticks_us()
    detect = sensor.snapshot(chn=0, timeout=1000)
    full = sensor.snapshot(chn=1, timeout=1000) if capture_chn1 else None
    return captured, detect, full


def _record(index, captured, proc_us, detect_path, full_path):
    return {
        "seq": index,
        "t_cap_us": captured,
        "status": 1,
        "u_px": None,
        "pos_raw": None,
        "pos_filt": None,
        "vel": None,
        "quality": None,
        "proc_us": proc_us,
        "image": detect_path,
        "image_chn1": full_path,
        "detect_format": "GRAYSCALE",
        "detect_width": DETECT_WIDTH,
        "detect_height": DETECT_HEIGHT,
        "chn1_format": "YUV420SP" if full_path is not None else None,
        "chn1_width": SENSOR_WIDTH if full_path is not None else None,
        "chn1_height": SENSOR_HEIGHT if full_path is not None else None,
    }


def _write_record(output_dir, index, record):
    with open("%s/frame_%06d.json" % (output_dir, index), "w") as stream:
        json.dump(record, stream)


def _capture_single(sensor, output_dir, count, capture_chn1):
    for index in range(count):
        guard_tick()
        started = _ticks_us()
        captured, detect, full = _snapshot_pair(sensor, capture_chn1)
        detect_path = "%s/frame_%06d.png" % (output_dir, index)
        full_path = "%s/frame_%06d_chn1.png" % (output_dir, index) if full is not None else None
        detect_path = _save_image(detect, detect_path, "GRAYSCALE")
        if full is not None:
            full_path = _save_image(full, full_path, "YUV420SP")
        _write_record(output_dir, index, _record(
            index, captured, time.ticks_diff(_ticks_us(), started), detect_path, full_path
        ))


def _capture_sequence(sensor, output_dir, count, capture_chn1):
    if count > MAX_SEQUENCE_FRAMES:
        raise ValueError("sequence count exceeds %d" % MAX_SEQUENCE_FRAMES)
    if capture_chn1:
        raise ValueError("sequence mode currently requires --no-capture-chn1")
    frames = []
    for index in range(count):
        guard_tick()
        captured, detect, full = _snapshot_pair(sensor, capture_chn1)
        frames.append((captured, detect.to_numpy_ref().tobytes()))
    for index, item in enumerate(frames):
        guard_tick()
        captured, detect_bytes = item
        detect_path = "%s/frame_%06d.gray.bin" % (output_dir, index)
        full_path = None
        started = _ticks_us()
        with open(detect_path, "wb") as stream:
            stream.write(detect_bytes)
        _write_record(output_dir, index, _record(
            index, captured, time.ticks_diff(_ticks_us(), started), detect_path, full_path
        ))
    del frames
    gc.collect()


def capture(mode="single", count=3, output_dir="/sdcard/hcv/capture", capture_chn1=True,
            deadline_s=120):
    if count <= 0:
        raise ValueError("count must be positive")
    try:
        os.mkdir(output_dir)
    except OSError:
        pass
    guard_start(deadline_s)
    sensor = None
    media_started = False
    try:
        sensor = _make_sensor(capture_chn1)
        media_started = True
        if mode == "single":
            _capture_single(sensor, output_dir, count, capture_chn1)
        elif mode == "sequence":
            _capture_sequence(sensor, output_dir, count, capture_chn1)
        else:
            raise ValueError("mode must be single or sequence")
    finally:
        if sensor is not None:
            sensor.stop()
        if media_started:
            MediaManager.deinit()


if __name__ == "__main__":
    capture()
