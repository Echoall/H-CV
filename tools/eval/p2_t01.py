"""Reproducible host evaluation for the P2-T01 detectors."""

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from k230.hcv.detect_blob import BlobDetector
from k230.hcv.detect_profile import ProfileDetector
from tools.synth.generate import generate_scene


PARAMS = {
    "diameter_px": 20,
    "gate_px": 20,
    "snr_min": 6,
    "snr_good": 20,
    "noise_floor": 0.5,
    "background_alpha": 0.05,
    "polarity": "both",
    "background_recovery_frames": 30,
    "background_recovery_alpha": 0.05,
}


def percentile(values, q):
    return float(np.percentile(np.asarray(values, dtype=float), q)) if values else None


def summarize(rows, key):
    groups = {}
    for row in rows:
        groups.setdefault(row[key], []).append(row)
    result = []
    for value, items in sorted(groups.items(), key=lambda pair: str(pair[0])):
        errors = [float(item["error_px"]) for item in items if item["found"]]
        absolute = [abs(error) for error in errors]
        result.append({
            "group": value,
            "samples": len(items),
            "found": sum(bool(item["found"]) for item in items),
            "rms_px": math.sqrt(sum(error * error for error in errors) / len(errors))
            if errors else None,
            "p95_abs_px": percentile(absolute, 95),
            "max_abs_px": max(absolute) if absolute else None,
        })
    return result


def run_profile(output):
    rows = []
    positions = (-120, -60, 0, 60, 120)
    for polarity in ("dark", "bright"):
        for highlight in (False, True):
            for theta in (0, 10, 20):
                for blur in (0, 6):
                    for position in positions:
                        for seed in (11, 12):
                            reference_image, _ = generate_scene(
                                ball_x=-1000,
                                theta_deg=theta,
                                noise=0,
                                highlight=False,
                                magnet_shadow=False,
                            )
                            image, truth = generate_scene(
                                ball_s_mm=position,
                                polarity=polarity,
                                highlight=highlight,
                                theta_deg=theta,
                                vel_px_per_s=2000 if blur else 0,
                                exposure_us=3000 if blur else 0,
                                noise=2,
                                seed=seed,
                            )
                            detector = ProfileDetector(
                                np.mean(reference_image[166:214], axis=0),
                                PARAMS,
                            )
                            detector.update_image(image[166:214])
                            rows.append({
                                "kind": "ball",
                                "polarity": polarity,
                                "highlight": int(highlight),
                                "theta_deg": theta,
                                "blur_px": blur,
                                "position_mm": position,
                                "seed": seed,
                                "found": int(detector.found),
                                "truth_px": truth["u_px"],
                                "measured_px": detector.u if detector.found else "",
                                "error_px": detector.u - truth["u_px"]
                                if detector.found else "",
                                "snr": detector.snr,
                            })
    for gain in (0.8, 1.0, 1.25):
        for offset in (-20, 0, 20):
            reference_image, _ = generate_scene(
                ball_x=-1000, noise=2, highlight=False,
                magnet_shadow=False, seed=11,
            )
            image, _ = generate_scene(
                ball_x=-1000, noise=2, highlight=False,
                magnet_shadow=False, seed=12,
            )
            changed = np.clip(image.astype(float) * gain + offset, 0, 255)
            detector = ProfileDetector(
                np.mean(reference_image[166:214], axis=0), PARAMS
            )
            detector.update_image(changed[166:214])
            rows.append({
                "kind": "empty_exposure",
                "polarity": "both",
                "highlight": 0,
                "theta_deg": 0,
                "blur_px": 0,
                "position_mm": "",
                "seed": 12,
                "gain": gain,
                "offset": offset,
                "found": int(detector.found),
                "truth_px": "",
                "measured_px": detector.u if detector.found else "",
                "error_px": "",
                "snr": detector.snr,
            })
    for kind in ("glare", "local_shadow"):
        for width in (12, 40, 60):
            for strength in (25, 40, 65):
                reference_image, _ = generate_scene(
                    ball_x=-1000, noise=0, highlight=False,
                    magnet_shadow=False,
                )
                image, _ = generate_scene(
                    ball_x=-1000,
                    noise=2,
                    highlight=False,
                    magnet_shadow=False,
                    seed=17,
                    **{kind: {
                        "x_px": 416,
                        "width_px": width,
                        "strength": strength,
                    }},
                )
                detector = ProfileDetector(
                    np.mean(reference_image[166:214], axis=0),
                    PARAMS,
                )
                detector.update_image(image[166:214])
                rows.append({
                    "kind": kind,
                    "polarity": "both",
                    "highlight": 0,
                    "theta_deg": 0,
                    "blur_px": 0,
                    "position_mm": "",
                    "seed": 17,
                    "gain": "",
                    "offset": "",
                    "width_px": width,
                    "strength": strength,
                    "found": int(detector.found),
                    "truth_px": "",
                    "measured_px": detector.u if detector.found else "",
                    "error_px": "",
                    "snr": detector.snr,
                })
    output.mkdir(parents=True, exist_ok=True)
    path = output / "profile-robust.csv"
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/data/P2-T01",
    )
    args = parser.parse_args()
    start = time.perf_counter()
    rows = run_profile(args.output)
    elapsed_ms = (time.perf_counter() - start) * 1000
    balls = [row for row in rows if row["kind"] == "ball"]
    empties = [row for row in rows if row["kind"] == "empty_exposure"]
    interference = [
        row for row in rows if row["kind"] in ("glare", "local_shadow")
    ]
    report = {
        "frames": len(rows),
        "ball_frames": len(balls),
        "empty_exposure_frames": len(empties),
        "empty_false_positives": sum(row["found"] for row in empties),
        "interference_frames": len(interference),
        "interference_false_positives": sum(row["found"] for row in interference),
        "ball_summary": summarize(balls, "polarity"),
        "elapsed_ms": elapsed_ms,
    }
    (args.output / "profile-robust.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
