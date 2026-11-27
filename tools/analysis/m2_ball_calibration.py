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
from k230.hcv.ruler import detect_long_ticks
from tools.calib.ruler_lut import BALL_POSITIONS_MM, build_ball_calibration
from tools.synth.generate import generate_scene
from tools.synth.ruler import generate_ruler

SCALE = 1.71
SCENE = dict(width=640, height=96, ball_y=50, scale_px_per_mm=SCALE,
             marker_positions_mm=(), magnet_shadow=False)
NOISE = 1.2
TRAIN_SEEDS = range(60)
VALIDATION_SEED = 10_000


def _roi_image(image, roi):
    x, y = roi["x"], roi["y"]
    return image[y:y + roi["h"], x:x + roi["w"]]


def _column(detector, image, roi):
    pixels = _roi_image(image, roi)
    profile = np.rint(pixels.mean(axis=0)).astype(np.uint8)
    detector.update(profile, roi_pixels=pixels.tobytes())
    return float(detector.u) + roi["x"] if detector.found else None


def _background(image, roi):
    return np.rint(_roi_image(image, roi).mean(axis=0)).astype(int).tolist()


def _configuration():
    config = json.loads((ROOT / "reports/data/M1/m1-config.json").read_text(encoding="utf-8"))
    config["chn_detect"]["height"] = 96
    config["roi"] = {"x": 65, "y": 20, "w": 510, "h": 48}
    return config


def synthetic_ball_calibration():
    config = _configuration()
    roi = config["roi"]
    params = config["detector"]["params"]
    empty, _ = generate_scene(ball_x=-100, noise=0, **SCENE)
    background = _background(empty, roi)
    reference = {"fingerprint": calibration_fingerprint(config), "samples": len(TRAIN_SEEDS),
                 "bg_profile": background}
    samples = {}
    for mm in BALL_POSITIONS_MM:
        detector = ProfileDetector(background, params)
        columns = []
        for seed in TRAIN_SEEDS:
            image, _ = generate_scene(ball_s_mm=mm, noise=NOISE, seed=seed, **SCENE)
            columns.append(_column(detector, image, roi))
        samples[mm] = columns

    lut = build_ball_calibration(samples, reference, config, 0.0)
    pixels, physical, _ = runtime_calibration(lut, config)
    errors = []
    for index, mm in enumerate(range(-120, 121)):
        seed = VALIDATION_SEED + index
        image, truth = generate_scene(ball_s_mm=mm, noise=NOISE, seed=seed, **SCENE)
        column = _column(ProfileDetector(background, params), image, roi)
        if column is None:
            raise ValueError(f"validation detector missed {mm:+d} mm (seed {seed})")
        prediction = interpolate_lut(column - roi["x"], pixels, physical)
        errors.append({
            "x_mm": mm, "seed": seed, "true_u_px": truth["u_px"], "detected_u_px": column,
            "estimated_mm": prediction, "error_mm": prediction - mm,
        })
    return {
        "theta_deg": 0, "scale_px_per_mm": SCALE, "noise_gray_std": NOISE,
        "training_seeds_inclusive": [TRAIN_SEEDS.start, TRAIN_SEEDS.stop - 1],
        "fingerprint": lut["fingerprint"], "method": lut["method"], "roi": roi,
        "anchors": lut["anchors"], "samples_px": samples, "per_mm_errors": errors,
        "max_abs_error_mm": max(abs(row["error_mm"]) for row in errors),
        "rms_error_mm": mean(row["error_mm"] ** 2 for row in errors) ** 0.5,
    }


def coordinate_bias():
    config = _configuration()
    rois = [config["roi"], dict(config["roi"], x=0, w=SCENE["width"])]
    empty, _ = generate_scene(ball_x=-100, noise=0, **SCENE)
    backgrounds = [_background(empty, roi) for roi in rois]
    cases = []
    for polarity in ("dark", "bright"):
        params = dict(config["detector"]["params"], polarity=polarity)
        for highlight in (False, True):
            offsets = [[], []]
            for index, mm in enumerate(range(-120, 121, 5)):
                image, truth = generate_scene(
                    ball_s_mm=mm, polarity=polarity, highlight=highlight,
                    noise=NOISE, seed=20_000 + index, **SCENE,
                )
                for roi, background, values in zip(rois, backgrounds, offsets):
                    column = _column(ProfileDetector(background, params), image, roi)
                    if column is None:
                        raise ValueError(f"bias detector missed {polarity} at {mm:+d} mm")
                    values.append(column - truth["u_px"])
            cases.append({
                "polarity": polarity, "highlight": highlight, "frames": len(offsets[0]),
                "roi_x": [roi["x"] for roi in rois], "mean_bias_px": list(map(mean, offsets)),
                "max_roi_difference_px": max(abs(a - b) for a, b in zip(*offsets)),
                "bias_by_roi_px": offsets,
            })
    ruler_phase = []
    for center in (320.0, 320.25, 320.5, 320.75):
        image, truth = generate_ruler(0.4, center_px=center, scale_px_per_mm=SCALE, seed=42)
        ticks = detect_long_ticks(image[102:111], round(truth[-120]), round(truth[120]))
        errors = [column - truth[mm] for mm, column in ticks]
        ruler_phase.append({
            "center_px": center, "ticks": len(ticks),
            "mean_bias_px": mean(errors), "max_abs_error_px": max(map(abs, errors)),
        })
    return {"ball_cases": cases, "ruler_phase": ruler_phase}


if __name__ == "__main__":
    print(json.dumps(dict(synthetic_ball_calibration(), coordinate_bias=coordinate_bias()), indent=2))
