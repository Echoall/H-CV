"""Measure the published crop from the M2 paper-ruler rehearsal."""

import argparse
import json
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from k230.hcv.ruler import _long_tick_candidates, detect_long_ticks


CROP_X = 24
CROP_Y = 190
ENDPOINTS = (69 - CROP_X, 510 - CROP_X)
BANDS = ((190, 200), (192, 201), (194, 202), (195, 202), (196, 202), (197, 202),
         (198, 203), (199, 203), (200, 204), (200, 206), (201, 206), (202, 208))
MEASUREMENT_BAND = (197, 202)
SEAM_COLUMN = 292
SEAM_MARGIN = 3


def measure_tick(profile, center):
    """Measure the valley at a visible tick against the white flanks."""
    center = round(center)
    left_white = profile[center - 7:center - 3]
    right_white = profile[center + 4:center + 8]
    baseline = min(np.median(left_white), np.median(right_white))
    noise = np.std(np.concatenate([
        left_white - np.median(left_white), right_white - np.median(right_white),
    ]), ddof=1)
    peak = center - 2 + np.argmin(profile[center - 2:center + 3])
    depth = baseline - profile[peak]
    if depth <= 0:
        raise ValueError(f"no dark valley at column {center + CROP_X}")
    threshold = baseline - depth / 2
    left = right = peak
    while left > 0 and profile[left - 1] < threshold:
        left -= 1
    while right + 1 < len(profile) and profile[right + 1] < threshold:
        right += 1
    if left == 0 or right + 1 == len(profile):
        raise ValueError(f"valley reaches the crop edge at column {center + CROP_X}")
    left_edge = left - (threshold - profile[left]) / (profile[left - 1] - profile[left])
    right_edge = right + (threshold - profile[right]) / (profile[right + 1] - profile[right])
    return {
        "column": int(peak) + CROP_X,
        "half_depth_width_px": round(float(right_edge - left_edge), 2),
        "depth_gray": round(float(depth), 2),
        "relative_depth_pct": round(float(100 * depth / baseline), 2),
        "flank_std_gray": round(float(noise), 2),
    }


def periodic_band(segment, pitch):
    """Select rows with a strong 1 cm period while discounting its 0.5 cm harmonic."""
    columns = np.arange(segment.shape[1])
    window = np.hanning(len(columns))
    waves = np.exp(-2j * np.pi * np.outer(np.array([1, 2]) / pitch, columns))
    best_score = -np.inf
    for top in range(3, 10):
        for bottom in range(top + 1, min(top + 4, 11) + 1):
            profile = segment[top:bottom].mean(axis=0)
            trend = np.polyval(np.polyfit(columns, profile, 1), columns)
            strength = 2 * np.abs(waves @ ((profile - trend) * window)) / window.sum()
            score = strength[0] - 0.65 * strength[1]
            if score > best_score:
                best_score = score
                best = profile, {
                    "aligned_rows": [top, bottom],
                    "amplitude_1cm_gray": round(float(strength[0]), 2),
                    "amplitude_05cm_gray": round(float(strength[1]), 2),
                }
    return best


def adaptive_strip(crop, pitch):
    # The bottom rule of the printed scale is the darkest median row in this
    # part of the crop. Interpolate between patches, retaining the two pieces'
    # local alignment near the seam. Integer sampling preserves contrast.
    columns = np.arange(crop.shape[1])
    patches = [
        (start + 19.5, 9 + np.argmin(np.median(crop[9:16, start:start + 40], axis=1)))
        for start in range(0, crop.shape[1] - 39, 40)
    ]
    baseline = np.interp(columns, *np.asarray(patches).T)
    shifts = np.rint(baseline - 12).astype(int)
    rows = np.clip(np.arange(crop.shape[0])[:, None] + shifts, 0, crop.shape[0] - 1)
    aligned = crop[rows, columns]
    selected = [periodic_band(segment, pitch) for segment in np.array_split(aligned, 3, axis=1)]
    profile = np.concatenate([profile for profile, _ in selected])
    ticks, _ = _long_tick_candidates(profile, *ENDPOINTS, min_depth=0.10)
    return {
        "baseline_rows": [[float(x + CROP_X), int(y) + CROP_Y] for x, y in patches],
        "selected_segments": [description for _, description in selected],
        "raw_candidate_count": len(ticks),
        "numbered_non_seam_candidates": [
            {"relative_mm": mm, "column": round(col + CROP_X, 2)}
            for mm, col in ticks if abs(col + CROP_X - SEAM_COLUMN) > SEAM_MARGIN
        ],
        "excluded_seam_columns": [
            round(col + CROP_X, 2) for _, col in ticks
            if abs(col + CROP_X - SEAM_COLUMN) <= SEAM_MARGIN
        ],
    }


def valley_spacing(crop, top, bottom):
    profile = crop[top:bottom, ENDPOINTS[0]:ENDPOINTS[1] + 1].mean(axis=0)
    radius = 4
    valleys = []
    for x in range(radius, len(profile) - radius):
        if not profile[x] < profile[x - 1] or profile[x] > profile[x + 1]:
            continue
        shoulder = min(max(profile[x - radius:x]), max(profile[x + 1:x + radius + 1]))
        if shoulder - profile[x] > 3:
            valleys.append(x)
    return {
        "crop_rows": [top, bottom],
        "valley_count": len(valleys),
        "median_spacing_px": float(np.median(np.diff(valleys))) if len(valleys) >= 2 else None,
    }


def analyze(crop):
    if crop.shape != (18, 552):
        raise ValueError("expected rows [190,208) and columns [24,576) of the source frame")
    bands = []
    for top, bottom in BANDS:
        strip = crop[top - CROP_Y:bottom - CROP_Y]
        profile = strip.mean(axis=0)
        ticks, pitch = _long_tick_candidates(profile, *ENDPOINTS, min_depth=0.10)
        try:
            detect_long_ticks(strip, *ENDPOINTS)
        except ValueError as error:
            result = str(error)
        else:
            result = "accepted"
        band = {
            "rows": [top, bottom],
            "candidate_count": len(ticks),
            "candidate_columns": [round(col + CROP_X, 2) for _, col in ticks],
            "result": result,
        }
        if (top, bottom) == MEASUREMENT_BAND:
            # The 150 mm position on this paper is a seam between two strips.
            band["visible_ticks"] = [
                measure_tick(profile, col) for _, col in ticks
                if abs(col + CROP_X - SEAM_COLUMN) > SEAM_MARGIN
            ]
        bands.append(band)
    # Compare to a fixed strip near the long ticks before aligning the paper.
    row = crop[198 - CROP_Y]
    fixed, _ = _long_tick_candidates(row, *ENDPOINTS, min_depth=0.10)
    return {
        "scale_px_per_mm": round(pitch / 10, 4),
        "max_gray": int(crop.max()),
        "pixels_at_or_above_250": int((crop >= 250).sum()),
        "bands": bands,
        "diagnostic_valley_spacing": [
            valley_spacing(crop, top, bottom) for top, bottom in ((0, 4), (4, 9), (5, 12))
        ],
        "fixed_row_198_count": len(fixed),
        "adaptive_strip": adaptive_strip(crop, pitch),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("crop", type=Path)
    args = parser.parse_args()
    image = cv2.imdecode(np.fromfile(args.crop, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        parser.error("cannot read crop")
    print(json.dumps(analyze(image), indent=2))
