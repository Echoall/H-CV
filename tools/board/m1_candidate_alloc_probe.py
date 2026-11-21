"""Measure the allocation of the board pipeline while tracking a known ROI."""

import math
import sys

from image import GRAYSCALE, Image
from ulab import numpy as np

sys.path.insert(0, "/sdcard/hcv")
for name in list(sys.modules):
    if name == "main_hcv" or name == "hcv" or name.startswith("hcv."):
        del sys.modules[name]

import main_hcv
from hcv.config import load_config
from hcv.guard import start, tick


def run(wide=False):
    start(5)
    config = load_config("/sdcard/hcv")
    roi = config["roi"]
    width, height = roi["w"], roi["h"]
    center = width // 2
    bounds = (0, 0, width, height)
    background = np.array(
        [110 + col * 8 // 100 if wide else 130 + col // 64 for col in range(width)],
        dtype=np.uint8,
    )
    empty = Image(width, height, GRAYSCALE)
    target = Image(width, height, GRAYSCALE)
    weak = Image(width, height, GRAYSCALE)
    empty_view = empty.to_numpy_ref()
    target_view = target.to_numpy_ref()
    weak_view = weak.to_numpy_ref()
    lit = background
    if wide:
        band_center = width - 80
        lit = np.array([
            int(background[col]) - int(60 * math.exp(-((col - band_center) / 30.0) ** 2))
            for col in range(width)
        ], dtype=np.uint8)
    for row in range(height):
        tick()
        empty_view[row, :] = background
        target_view[row, :] = lit
        weak_view[row, :] = lit
    middle = height // 2
    target_view[middle - 9:middle + 9, center - 8:center + 8] = 55
    weak_view[middle - 9:middle + 9, center - 8:center + 8] = 120
    if not wide:
        target_view[:, center - 172:center - 156] = 40
        weak_view[:, center - 172:center - 156] = 40

    profile = main_hcv._write_column_profile
    frame = 0

    def fixture_profile(_image, _bounds, pixels, profile_pixels):
        nonlocal frame
        if frame < 5:
            source = empty
        else:
            source = weak if frame % 2 else target
        frame += 1
        return profile(source, bounds, pixels, profile_pixels)

    main_hcv._write_column_profile = fixture_profile
    try:
        main_hcv.run(max_runtime_s=15, max_frames=60, allocation_probe=True)
        frame = 0
        main_hcv.run(max_runtime_s=15)
    finally:
        main_hcv._write_column_profile = profile


run(wide=globals().get("WIDE_BAND", False))
