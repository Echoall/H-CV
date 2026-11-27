"""Build the board's linear pixel-to-mm LUT from an empty reference capture."""

import argparse
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from k230.hcv.calibration import runtime_calibration
from k230.hcv.config import calibration_fingerprint
from k230.hcv.geometry import interpolate_lut
from k230.hcv.ruler import detect_long_ticks

BALL_POSITIONS_MM = (-120, -100, -50, 0, 50, 100, 120)


def _calibration_record(anchors, markers, reference, config, level_deg, method):
    if not abs(level_deg) <= 0.2:
        raise ValueError("level the ruler to within ±0.2° before calibration")
    fingerprint = calibration_fingerprint(config)
    if reference["fingerprint"] != fingerprint or reference["samples"] < 60:
        raise ValueError("need at least 60 reference frames from the same camera configuration")
    result = {
        "schema": 1,
        "method": method,
        "id": uuid4().int % 65535 + 1,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fingerprint": fingerprint,
        "conditions": {"rod_level_deg": level_deg, "light": "normal", "operator_note": ""},
        "anchors": anchors,
        "markers_ref": markers,
        "bg_profile": reference["bg_profile"],
        "interp": "linear",
    }
    pixels, positions, _ = runtime_calibration(result, config)
    errors = [
        interpolate_lut(pixels[index], (pixels[index - 1], pixels[index + 1]),
                        (positions[index - 1], positions[index + 1])) - positions[index]
        for index in range(1, len(anchors) - 1)
    ]
    result["validation"] = {
        "cv_max_err_mm": max(map(abs, errors)),
        "cv_rms_mm": math.sqrt(statistics.mean(error**2 for error in errors)),
        "monotonic": True,
    }
    return result


def build_calibration(image, selection, reference, config, level_deg):
    """Selection contains the approximate endpoint columns and strip row limits."""
    left, right, top, bottom = selection
    camera = config["chn_detect"]
    if image.shape != (camera["height"], camera["width"]):
        raise ValueError("reference image dimensions differ from the detection channel")
    if not 0 <= top < bottom <= image.shape[0]:
        raise ValueError("the long-tick strip must be inside the reference image")
    ticks = detect_long_ticks(image[top:bottom], left, right)
    anchors = [
        {"x_mm": float(x), "u_px": float(u), "n": 1, "std_px": 0.0} for x, u in ticks
    ]
    markers = [
        {"name": f"tick_{x:+d}", "x_mm": float(x), "u_px": float(u), "v_px": (top + bottom - 1) / 2}
        for x, u in ticks
    ]
    return _calibration_record(anchors, markers, reference, config, level_deg, "ruler")


def ball_anchor(position_mm, samples):
    """Summarize full-image detector columns. Missing detections are None."""
    columns = [u for u in samples if u is not None]
    if any(type(u) not in (int, float) or not math.isfinite(u) for u in columns):
        raise ValueError(f"{position_mm:+d} mm contains invalid detector columns")
    if len(columns) < 60:
        raise ValueError(f"{position_mm:+d} mm needs at least 60 detected frames ({len(columns)} recorded)")
    deviation = statistics.stdev(columns)
    if deviation > 0.3:
        raise ValueError(f"{position_mm:+d} mm is unstable (std {deviation:.3f} px > 0.3 px)")
    return {"x_mm": position_mm, "u_px": statistics.median(columns),
            "n": len(columns), "std_px": deviation}


def build_ball_calibration(samples, reference, config, level_deg):
    if set(samples) != set(BALL_POSITIONS_MM):
        raise ValueError("ball calibration needs exactly seven distinct positions: -120 -100 -50 0 50 100 120 mm")
    anchors = [ball_anchor(mm, samples[mm]) for mm in BALL_POSITIONS_MM]
    return _calibration_record(anchors, [], reference, config, level_deg, "ball")


def compare_calibrations(ruler, ball, samples, config):
    ruler_lut = runtime_calibration(ruler, config)[:2]
    ball_lut = runtime_calibration(ball, config)[:2]
    if set(samples) != set(BALL_POSITIONS_MM):
        raise ValueError("comparison needs fresh samples at all seven positions")
    results = []
    for mm in BALL_POSITIONS_MM:
        anchor = ball_anchor(mm, samples[mm])
        columns = [value - config["roi"]["x"] for value in samples[mm] if value is not None]
        ruler_mean = statistics.mean(interpolate_lut(value, *ruler_lut) for value in columns)
        ball_mean = statistics.mean(interpolate_lut(value, *ball_lut) for value in columns)
        results.append(dict(
            anchor, ruler_mean_mm=ruler_mean, ball_mean_mm=ball_mean,
            ruler_error_mm=ruler_mean - mm, ball_error_mm=ball_mean - mm,
            ruler_minus_ball_mm=ruler_mean - ball_mean,
        ))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--strip", type=int, nargs=4, default=None,
                        metavar=("LEFT", "RIGHT", "TOP", "BOTTOM"), required=True)
    parser.add_argument("--level-deg", type=float, required=True)
    args = parser.parse_args()
    image = cv2.imdecode(np.fromfile(args.image, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    result = build_calibration(
        image, args.strip, json.loads(args.reference.read_text(encoding="utf-8")),
        json.loads(args.config.read_text(encoding="utf-8")), args.level_deg,
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
