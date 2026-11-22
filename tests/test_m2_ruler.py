import json
from pathlib import Path

import numpy as np
import pytest

from k230.hcv.calibration import runtime_calibration
from k230.hcv.config import calibration_fingerprint
from k230.hcv.geometry import interpolate_lut
from k230.hcv.ruler import detect_long_ticks
from tools.calib.ruler_lut import build_calibration
from tools.synth.ruler import generate_ruler


def synthetic_ruler(missing=()):
    columns = np.arange(640)
    rng = np.random.default_rng(19)
    image = 210 + 0.04 * columns + rng.normal(0, 1.2, size=(360, 640))
    ticks = {mm: 320.25 + 1.7 * mm + 0.0002 * mm**2 for mm in range(-120, 121, 10)}
    for mm, center in ticks.items():
        if mm not in missing:
            image[93:112] -= 175 * np.exp(-((columns - center) / 1.25) ** 2)
        for shift in (-3.4, 3.4):
            image[93:101] -= 110 * np.exp(-((columns - center - shift) / 0.8) ** 2)
    return np.clip(image, 0, 255).astype(np.uint8), ticks


def test_numbered_ruler_keeps_missing_ticks_from_shifting_ids():
    image, truth = synthetic_ruler(missing=(-60, 0, 90))
    ticks = detect_long_ticks(image[103:110], 119, 527)
    assert len(ticks) == 22
    assert {mm for mm, _ in ticks} == set(truth) - {-60, 0, 90}
    assert max(abs(column - truth[mm]) for mm, column in ticks) < 0.3


def test_ruler_refuses_a_strip_with_too_few_ticks():
    image, _ = synthetic_ruler(missing=(-120, -100, -80, -60, -40, -20))
    with pytest.raises(ValueError, match="at least 20"):
        detect_long_ticks(image[103:110], 119, 527)


@pytest.mark.parametrize("line_width_mm", (0.2, 0.4))
@pytest.mark.parametrize("obscured", ((), (-10,), (-120, -20), (-60, 0, 120)))
@pytest.mark.parametrize("center", (320.05, 320.25, 320.48))
def test_mvp_ruler_accuracy(line_width_mm, obscured, center):
    scale = 1.71
    image, truth = generate_ruler(line_width_mm, obscured, seed=11,
                                  center_px=center, scale_px_per_mm=scale)
    left, right = int(truth[-120]), int(truth[120])
    ticks = detect_long_ticks(image[102:111], left, right)
    assert len(ticks) >= 20
    assert {mm for mm, _ in ticks} == set(truth) - set(obscured)
    assert max(abs(column - truth[mm]) for mm, column in ticks) < 0.3
    positions, columns = map(list, zip(*ticks))
    for mm in range(-120, 121):
        pixel = center + scale * mm
        measured = interpolate_lut(pixel, columns, positions)
        assert abs(measured - mm) < 0.3


@pytest.mark.parametrize("artifact, missing", [
    ("seam", {0}),
    ("glare", {-50, -40}),
    ("half_centimeters", {-20, 10}),
])
def test_ruler_grid_does_not_number_artifacts(artifact, missing):
    columns = np.arange(640)
    expected = {mm: 320.25 + 1.71 * mm for mm in range(-120, 121, 10)}
    rng = np.random.default_rng(43)
    profile = np.full(640, 209.0)
    for mm, center in expected.items():
        if mm not in missing:
            profile -= 19 * np.exp(-((columns - center) / 0.90) ** 2)
    if artifact == "seam":
        # Dark, narrow and close to the absent 0 tick.
        profile -= 116 * np.exp(-((columns - (expected[0] + 1.25)) / 0.90) ** 2)
    elif artifact == "glare":
        profile += 80 * np.exp(-((columns - (expected[-50] + 8.55)) / 12) ** 2)
    else:
        for mm in (-75, -35, -25, -15, 5, 15, 55, 85):
            profile -= 18 * np.exp(-((columns - (320.25 + 1.71 * mm)) / 0.90) ** 2)
    strip = np.clip(profile + rng.normal(0, 1.8, (11, 640)), 0, 255).astype(np.uint8)
    ticks = detect_long_ticks(strip, int(expected[-120]), int(expected[120]))
    assert {mm for mm, _ in ticks} == set(expected) - missing
    assert max(abs(x - expected[mm]) for mm, x in ticks) < 0.3


@pytest.mark.parametrize("ambiguous", [False, True])
def test_ruler_refuses_blank_or_ambiguous_half_centimeter_strip(ambiguous):
    rng = np.random.default_rng(27)
    columns = np.arange(640)
    profile = 208.0 + 0.035 * columns
    if ambiguous:
        for mm in range(-120, 121, 5):
            profile -= 19 * np.exp(-((columns - (320.25 + 1.71 * mm)) / 0.90) ** 2)
    strip = (profile + rng.normal(0, 1.8, (11, 640))).astype(np.uint8)
    with pytest.raises(ValueError, match="at least 20"):
        detect_long_ticks(strip, 115, 525)


def test_calibration_moves_full_image_anchors_into_roi_and_rejects_stale_profile():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "reports/data/M1/m1-config.json").read_text())
    config["roi"].update(x=60, w=520)
    reference = {"bg_profile": [150] * 520, "samples": 60,
                 "fingerprint": calibration_fingerprint(config)}
    image, truth = synthetic_ruler()
    value = build_calibration(image, (119, 527, 103, 110), reference, config, 0)
    columns, positions, profile = runtime_calibration(value, config)
    assert positions == sorted(truth)
    assert np.asarray(columns) == pytest.approx(
        [truth[mm] - 60 for mm in positions], abs=0.4
    )
    assert value["validation"]["cv_max_err_mm"] < 0.5
    assert profile == reference["bg_profile"]
    config["roi"]["y"] += 1
    with pytest.raises(ValueError, match="another camera configuration"):
        runtime_calibration(value, config)
