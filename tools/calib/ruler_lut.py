"""Build the board's linear pixel-to-mm LUT from an empty reference capture."""

import argparse
import json
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
from k230.hcv.ruler import detect_long_ticks


def build_calibration(image, selection, reference, config, level_deg):
    """Selection contains the approximate endpoint columns and strip row limits."""
    left, right, top, bottom = selection
    camera = config["chn_detect"]
    if image.shape != (camera["height"], camera["width"]):
        raise ValueError("reference image dimensions differ from the detection channel")
    if not 0 <= top < bottom <= image.shape[0]:
        raise ValueError("the long-tick strip must be inside the reference image")
    if not abs(level_deg) <= 0.2:
        raise ValueError("level the ruler to within ±0.2° before calibration")
    fingerprint = calibration_fingerprint(config)
    if reference["fingerprint"] != fingerprint or reference["samples"] < 60:
        raise ValueError("need at least 60 reference frames from the same camera configuration")

    ticks = detect_long_ticks(image[top:bottom], left, right)
    mm, columns = np.asarray(ticks).T
    errors = []
    for index in range(1, len(ticks) - 1):
        x0, x1 = mm[index - 1], mm[index + 1]
        u0, u1 = columns[index - 1], columns[index + 1]
        estimate = x0 + (columns[index] - u0) * (x1 - x0) / (u1 - u0)
        errors.append(estimate - mm[index])
    result = {
        "schema": 1,
        "id": uuid4().int % 65535 + 1,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fingerprint": fingerprint,
        "conditions": {"rod_level_deg": level_deg, "light": "normal", "operator_note": ""},
        "anchors": [
            {"x_mm": float(x), "u_px": float(u), "n": 1, "std_px": 0.0}
            for x, u in ticks
        ],
        "markers_ref": [
            {"name": f"tick_{x:+d}", "x_mm": float(x), "u_px": float(u),
             "v_px": (top + bottom - 1) / 2}
            for x, u in ticks
        ],
        "bg_profile": reference["bg_profile"],
        "interp": "linear",
        "validation": {
            "cv_max_err_mm": float(np.max(np.abs(errors))),
            "cv_rms_mm": float(np.sqrt(np.mean(np.square(errors)))),
            "monotonic": True,
        },
    }
    runtime_calibration(result, config)
    return result


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
