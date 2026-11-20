"""CanMV capture helper for P1-T01.

Run manually from the board. It intentionally has a finite frame count and
uses the project's cooperative guard when that module is available.
"""

import gc
import json
import os
import time

import sensor


def _guard_start(seconds):
    try:
        from hcv.guard import start
        start(seconds)
    except ImportError:
        pass


def _guard_tick():
    try:
        from hcv.guard import tick
        tick()
    except ImportError:
        try:
            os.exitpoint()
        except AttributeError:
            pass


def _ticks_us():
    return time.ticks_us()


def _save_image(image, path):
    image.save(path, quality=100)


def _snapshot(channel):
    try:
        return sensor.snapshot(chn=channel)
    except TypeError:
        if channel == 0:
            return sensor.snapshot()
        raise


def _capture_one(output_dir, index, capture_chn1):
    _guard_tick()
    start = _ticks_us()
    image = _snapshot(0)
    captured = _ticks_us()
    path = "%s/frame_%06d.bmp" % (output_dir, index)
    _save_image(image, path)
    chn1_path = None
    if capture_chn1:
        chn1_path = "%s/frame_%06d_chn1.bmp" % (output_dir, index)
        _save_image(_snapshot(1), chn1_path)
    record = {
        "seq": index,
        "t_cap_us": captured,
        "status": 1,
        "u_px": None,
        "pos_raw": None,
        "pos_filt": None,
        "vel": None,
        "quality": None,
        "proc_us": time.ticks_diff(_ticks_us(), start),
        "capture_chn1": bool(capture_chn1),
        "image": path,
        "image_chn1": chn1_path,
    }
    with open("%s/frame_%06d.json" % (output_dir, index), "w") as stream:
        json.dump(record, stream)


def _capture_sequence(output_dir, count, capture_chn1):
    frames = []
    timestamps = []
    for index in range(count):
        _guard_tick()
        timestamps.append(_ticks_us())
        frames.append((_snapshot(0), _snapshot(1) if capture_chn1 else None))
    for index, pair in enumerate(frames):
        _guard_tick()
        image, chn1 = pair
        _save_image(image, "%s/frame_%06d.bmp" % (output_dir, index))
        if chn1 is not None:
            _save_image(chn1, "%s/frame_%06d_chn1.bmp" % (output_dir, index))
        with open("%s/frame_%06d.json" % (output_dir, index), "w") as stream:
            json.dump({
                "seq": index,
                "t_cap_us": timestamps[index],
                "status": 1,
                "u_px": None,
                "pos_raw": None,
                "pos_filt": None,
                "vel": None,
                "quality": None,
                "capture_chn1": bool(capture_chn1),
                "image": "%s/frame_%06d.bmp" % (output_dir, index),
                "image_chn1": (
                    "%s/frame_%06d_chn1.bmp" % (output_dir, index)
                    if capture_chn1 else None
                ),
            }, stream)
    del frames
    gc.collect()


def capture(mode="single", count=3, output_dir="/sdcard/hcv/capture", capture_chn1=False,
            deadline_s=120):
    _guard_start(deadline_s)
    try:
        os.makedirs(output_dir)
    except OSError:
        pass
    sensor.reset()
    sensor.set_pixformat(sensor.GRAYSCALE)
    sensor.set_framesize(sensor.HD720)
    sensor.skip_frames(time=2000)
    try:
        if mode == "single":
            for index in range(count):
                _capture_one(output_dir, index, capture_chn1)
        elif mode == "sequence":
            _capture_sequence(output_dir, count, capture_chn1)
        else:
            raise ValueError("mode must be single or sequence")
    finally:
        try:
            sensor.stop()
        except AttributeError:
            pass


if __name__ == "__main__":
    capture()
