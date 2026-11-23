"""Run the seven-point ball calibration against deterministic grayscale images."""

import json
from pathlib import Path
from statistics import mean
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from k230.hcv.calibration import runtime_calibration
from k230.hcv.config import calibration_fingerprint
from k230.hcv.detect_profile import ProfileDetector
from k230.hcv.geometry import interpolate_lut
from tools.calib.ruler_lut import BALL_POSITIONS_MM, build_ball_calibration
from tools.synth.generate import generate_scene


def synthetic_ball_calibration():
    config = json.loads((ROOT / "reports/data/M1/m1-config.json").read_text(encoding="utf-8"))
    config["chn_detect"]["height"] = 96
    config["roi"] = {"x": 65, "y": 20, "w": 510, "h": 48}
    scale = 1.71
    scene = dict(width=640, height=96, ball_y=50, scale_px_per_mm=scale,
                 marker_positions_mm=(), magnet_shadow=False)
    empty, _ = generate_scene(ball_x=-100, noise=0, **scene)
    background = np.rint(empty[20:68, 65:575].mean(axis=0)).astype(int).tolist()
    reference = {"fingerprint": calibration_fingerprint(config), "samples": 60,
                 "bg_profile": background}
    samples = {}
    for mm in BALL_POSITIONS_MM:
        detector = ProfileDetector(background, config["detector"]["params"])
        columns = []
        for seed in range(60):
            image, _ = generate_scene(ball_s_mm=mm, noise=1.2, seed=seed, **scene)
            roi = image[20:68, 65:575]
            profile = np.rint(roi.mean(axis=0)).astype(np.uint8)
            detector.update(profile, roi_pixels=roi.tobytes())
            columns.append(float(detector.u) + 65 if detector.found else None)
        samples[mm] = columns

    lut = build_ball_calibration(samples, reference, config, 0.0)
    pixels, physical, _ = runtime_calibration(lut, config)
    errors = [
        {"x_mm": mm, "error_mm": interpolate_lut(320 + scale * mm - 65, pixels, physical) - mm}
        for mm in range(-120, 121)
    ]
    return {
        "theta_deg": 0, "scale_px_per_mm": scale, "noise_gray_std": 1.2,
        "fingerprint": lut["fingerprint"], "method": lut["method"], "roi": config["roi"],
        "anchors": lut["anchors"], "samples_px": samples, "per_mm_errors": errors,
        "max_abs_error_mm": max(abs(row["error_mm"]) for row in errors),
        "rms_error_mm": mean(row["error_mm"] ** 2 for row in errors) ** 0.5,
    }


if __name__ == "__main__":
    print(json.dumps(synthetic_ball_calibration(), indent=2))
