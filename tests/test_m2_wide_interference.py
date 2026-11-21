import json
from itertools import product
from pathlib import Path

import numpy as np

from k230.hcv.detect_profile import ProfileDetector
from tools.synth.generate import generate_scene


ROI = slice(166, 214)


def column_profile(pixels):
    return (np.sum(pixels, axis=0, dtype=np.uint16) // pixels.shape[0]).astype(np.uint8)


def test_live_detector_finds_ball_under_960_interference_cases():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "reports/data/M1/m1-config.json").read_text())
    params = dict(config["detector"]["params"], m1_fast=True)
    empty, _ = generate_scene(ball_x=-1000, noise=0, highlight=False, magnet_shadow=False)
    background = column_profile(empty[ROI])
    columns = np.arange(640)
    failures = []
    count = 0
    for polarity, center, position in product(
        ("dark", "bright"), (150, 300, 500, 560), (-100, 60, 110)
    ):
        image, truth = generate_scene(ball_s_mm=position, seed=7, polarity=polarity)
        if abs(center - truth["u_px"]) < 40:
            continue
        for amplitude, width, sign in product((15, 25, 40, 60), (6, 12, 20, 30, 40, 60), (1, -1)):
            interference = sign * amplitude * np.exp(-((columns - center) / (width / 2.0)) ** 2)
            pixels = np.ascontiguousarray(
                np.clip(image[ROI].astype(float) + interference, 0, 255), dtype=np.uint8
            )
            detector = ProfileDetector(background, dict(params, polarity=polarity), width=640)
            detector.update(column_profile(pixels), predicted_u=None, roi_pixels=pixels.tobytes())
            count += 1
            if not detector.found or abs(detector.u - truth["u_px"]) > 1:
                failures.append((polarity, center, position, sign * amplitude, width, detector.u))

    assert count == 960
    assert failures == []
