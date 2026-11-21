"""Compare the M1 fast detector with the P2 detector on the full matrix."""

import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def _case(reference, image, kind, truth=None, polarity="both", **metadata):
    return {
        "reference": reference,
        "image": image,
        "kind": kind,
        "truth": truth,
        "polarity": polarity,
        **metadata,
    }


def build_cases():
    cases = []
    positions = (-120, -60, 0, 60, 120)
    for polarity in ("dark", "bright"):
        for highlight in (False, True):
            for theta in (0, 10, 20):
                reference, _ = generate_scene(
                    ball_x=-1000,
                    theta_deg=theta,
                    noise=0,
                    highlight=False,
                    magnet_shadow=False,
                )
                for blur in (0, 6):
                    for position in positions:
                        for seed in (11, 12):
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
                            cases.append(_case(
                                reference,
                                image,
                                "ball",
                                truth,
                                position_mm=position,
                                polarity=polarity,
                                highlight=int(highlight),
                                theta_deg=theta,
                                blur_px=blur,
                                seed=seed,
                            ))

    for gain in (0.8, 1.0, 1.25):
        for offset in (-20, 0, 20):
            reference, _ = generate_scene(
                ball_x=-1000,
                noise=2,
                highlight=False,
                magnet_shadow=False,
                seed=11,
            )
            image, _ = generate_scene(
                ball_x=-1000,
                noise=2,
                highlight=False,
                magnet_shadow=False,
                seed=12,
            )
            image = np.clip(
                image.astype(float) * gain + offset, 0, 255
            ).astype(np.uint8)
            cases.append(_case(
                reference,
                image,
                "empty_exposure",
                gain=gain,
                offset=offset,
            ))

    for kind in ("glare", "local_shadow"):
        for width in (12, 40, 60):
            for strength in (25, 40, 65):
                reference, _ = generate_scene(
                    ball_x=-1000,
                    noise=0,
                    highlight=False,
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
                cases.append(_case(
                    reference,
                    image,
                    "empty_interference",
                    interference=kind,
                    width_px=width,
                    strength=strength,
                ))

    for kind in ("glare", "local_shadow"):
        for polarity in ("dark", "bright"):
            for strength in (15, 25, 40, 60):
                for width in (6, 12, 20, 30, 40, 60):
                    reference, _ = generate_scene(
                        ball_x=-1000,
                        noise=0,
                        highlight=False,
                        magnet_shadow=False,
                    )
                    image, truth = generate_scene(
                        ball_s_mm=60,
                        polarity=polarity,
                        highlight=True,
                        noise=2,
                        seed=11,
                        **{kind: {
                            "x_px": 150,
                            "width_px": width,
                            "strength": strength,
                        }},
                    )
                    cases.append(_case(
                        reference,
                        image,
                        "ball_interference",
                        truth,
                        polarity=polarity,
                        interference=kind,
                        width_px=width,
                        strength=strength,
                    ))

    for ball_polarity, detector_polarity in (
        ("dark", "bright"),
        ("bright", "dark"),
    ):
        for position in (-120, -60, 0, 60, 120):
            for seed in (11, 12):
                reference, _ = generate_scene(
                    ball_x=-1000,
                    noise=0,
                    highlight=False,
                    magnet_shadow=False,
                )
                image, truth = generate_scene(
                    ball_s_mm=position,
                    polarity=ball_polarity,
                    highlight=True,
                    noise=2,
                    seed=seed,
                )
                cases.append(_case(
                    reference,
                    image,
                    "polarity_wrong",
                    truth,
                    polarity=detector_polarity,
                    ball_polarity=ball_polarity,
                    detector_polarity=detector_polarity,
                    position_mm=position,
                    seed=seed,
                ))
    return cases


def _profile(image, strategy):
    strip = image[166:214]
    if strategy == "single":
        return strip[strip.shape[0] // 2, :]
    if strategy == "rows8":
        middle = strip.shape[0] // 2
        return np.mean(strip[middle - 4:middle + 4], axis=0)
    if strategy == "rows48":
        return strip
    raise ValueError("unknown row strategy " + strategy)


def _run_case(case, fast, strategy):
    params = dict(PARAMS, m1_fast=fast, polarity=case["polarity"])
    detector = ProfileDetector(
        np.mean(case["reference"][166:214], axis=0)
        if strategy == "rows48"
        else _profile(case["reference"], strategy),
        params,
        width=640,
    )
    started = time.perf_counter()
    if strategy == "rows48":
        detector.update_image(case["image"][166:214])
    else:
        detector.update(_profile(case["image"], strategy))
    elapsed_us = (time.perf_counter() - started) * 1_000_000
    found = bool(detector.found)
    error = None
    truth = case["truth"]
    if found and truth is not None:
        error = float(detector.u - truth["u_px"])
    return {
        "kind": case["kind"],
        "found": int(found),
        "error_px": error,
        "elapsed_us": elapsed_us,
        "snr": float(detector.snr),
    }


def _percentile(values, q):
    return float(np.percentile(np.asarray(values), q)) if values else None


def _summarize(rows):
    output = {}
    for kind in sorted({row["kind"] for row in rows}):
        subset = [row for row in rows if row["kind"] == kind]
        errors = [row["error_px"] for row in subset if row["error_px"] is not None]
        absolute = [abs(value) for value in errors]
        output[kind] = {
            "samples": len(subset),
            "found": sum(row["found"] for row in subset),
            "false_positives": (
                sum(row["found"] for row in subset)
                if kind.startswith("empty")
                else None
            ),
            "misses": (
                sum(not row["found"] for row in subset)
                if kind.startswith("ball") or kind == "polarity_wrong"
                else None
            ),
            "rms_px": (
                math.sqrt(sum(value * value for value in errors) / len(errors))
                if errors else None
            ),
            "p95_abs_px": _percentile(absolute, 95),
            "max_abs_px": max(absolute) if absolute else None,
            "elapsed_p50_us": _percentile(
                [row["elapsed_us"] for row in subset], 50
            ),
            "elapsed_p95_us": _percentile(
                [row["elapsed_us"] for row in subset], 95
            ),
            "elapsed_p99_us": _percentile(
                [row["elapsed_us"] for row in subset], 99
            ),
            "elapsed_max_us": max(row["elapsed_us"] for row in subset),
        }
    return output


def main():
    output = Path("reports/data/M1/equivalence")
    output.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    report = {
        "cases": len(cases),
        "variants": {},
        "row_strategies": {},
    }
    for fast in (False, True):
        for strategy in ("rows48", "rows8", "single"):
            key = "fast=%s,%s" % (int(fast), strategy)
            rows = [_run_case(case, fast, strategy) for case in cases]
            report["variants"][key] = _summarize(rows)
            report["row_strategies"][key] = {
                "samples": len(rows),
                "found": sum(row["found"] for row in rows),
                "elapsed_p50_us": _percentile(
                    [row["elapsed_us"] for row in rows], 50
                ),
                "elapsed_p95_us": _percentile(
                    [row["elapsed_us"] for row in rows], 95
                ),
                "elapsed_max_us": max(row["elapsed_us"] for row in rows),
            }
    (output / "report.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
