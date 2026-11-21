"""Check the production profile and candidate path on known board images."""

import gc
import sys
import time

from image import GRAYSCALE, Image
from ulab import numpy as np

sys.path.insert(0, "/sdcard/hcv")
for name in list(sys.modules):
    if name == "main_hcv" or name == "hcv" or name.startswith("hcv."):
        del sys.modules[name]

from hcv.config import load_config
from hcv.detect_profile import ProfileDetector
from hcv.guard import start, tick
from main_hcv import _write_column_profile


def run():
    start(5)
    params = load_config("/sdcard/hcv")["detector"]["params"]
    width, height = 640, 48
    bounds = (0, 0, width, height)
    pixels = bytearray(width * height)
    profile_pixels = bytearray(width)
    profile = np.frombuffer(profile_pixels, dtype=np.uint8)
    background = np.array([130 + col // 64 for col in range(width)], dtype=np.uint8)
    source = Image(width, height, GRAYSCALE)
    view = source.to_numpy_ref()
    for label in ("empty", "shadow_only", "ball_and_shadow", "wrong_polarity"):
        tick()
        for row in range(height):
            tick()
            view[row, :] = background
        if label in ("shadow_only", "ball_and_shadow"):
            view[:, 148:164] = 40
        if label == "ball_and_shadow":
            view[15:33, 312:328] = 55
        if label == "wrong_polarity":
            view[15:33, 312:328] = 230
        detector = ProfileDetector(background, params, width=width)
        for attempt in range(3):
            tick()
            gc.collect()
            before = gc.mem_free()
            started = time.ticks_us()
            _write_column_profile(source, bounds, pixels, profile_pixels)
            detector.update(profile, roi_pixels=pixels)
            elapsed = time.ticks_diff(time.ticks_us(), started)
            allocated = before - gc.mem_free()
            print(
                "LIVE_PROBE,case=%s,try=%d,found=%d,u=%.3f,us=%d,alloc=%d"
                % (label, attempt, detector.found, detector.u, elapsed, allocated)
            )
            assert detector.found == (label == "ball_and_shadow")
            if detector.found:
                assert abs(detector.u - 319.5) < 1.0


run()
