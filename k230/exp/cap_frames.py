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


def _save_image(image, path):
    image.save(path, quality=100)


def _copy_image(image):
    copy_method = getattr(image, "copy", None)
    if copy_method is None:
        raise RuntimeError("CanMV Image.copy is required for sequence capture")
    return copy_method()


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
    }


def _write_record(output_dir, index, record):
    with open("%s/frame_%06d.json" % (output_dir, index), "w") as stream:
        json.dump(record, stream)


def _capture_single(sensor, output_dir, count, capture_chn1):
    for index in range(count):
        guard_tick()
        started = _ticks_us()
        captured, detect, full = _snapshot_pair(sensor, capture_chn1)
        detect_path = "%s/frame_%06d.bmp" % (output_dir, index)
        full_path = "%s/frame_%06d_chn1.bmp" % (output_dir, index) if full is not None else None
        _save_image(detect, detect_path)
        if full is not None:
            _save_image(full, full_path)
        _write_record(output_dir, index, _record(
            index, captured, time.ticks_diff(_ticks_us(), started), detect_path, full_path
        ))


def _capture_sequence(sensor, output_dir, count, capture_chn1):
    if count > MAX_SEQUENCE_FRAMES:
        raise ValueError("sequence count exceeds %d" % MAX_SEQUENCE_FRAMES)
    frames = []
    for index in range(count):
        guard_tick()
        captured, detect, full = _snapshot_pair(sensor, capture_chn1)
        frames.append((captured, _copy_image(detect), _copy_image(full) if full is not None else None))
    for index, item in enumerate(frames):
        guard_tick()
        captured, detect, full = item
        detect_path = "%s/frame_%06d.bmp" % (output_dir, index)
        full_path = "%s/frame_%06d_chn1.bmp" % (output_dir, index) if full is not None else None
        started = _ticks_us()
        _save_image(detect, detect_path)
        if full is not None:
            _save_image(full, full_path)
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
        os.makedirs(output_dir)
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
