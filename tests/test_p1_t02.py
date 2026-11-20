import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "k230"))

import numpy as np
import pytest

from tools.eval.evaluate import evaluate, summarize_errors
from tools.synth.generate import (
    apply_projective,
    fit_projective,
    generate_scene,
    inverse_u_to_s,
    project_s_to_u,
    write_scene,
)


def test_synth_truth_and_ideal_evaluation(tmp_path):
    image, truth = generate_scene(
        ball_x=210,
        polarity="bright",
        highlight=True,
        scratch=True,
        magnet_shadow=True,
        motion_blur=2,
        seed=7,
    )
    assert image.shape == (360, 640)
    assert 0 <= truth["u_px"] < image.shape[1]
    dataset = tmp_path / "dataset"
    write_scene(dataset / "scene_a", count=5, ball_x=210, polarity="bright", seed=7)
    summary, _ = evaluate(dataset, detector_name="ideal")
    assert summary["frames"] == 5
    assert summary["valid_rate"] == 1.0
    assert summary["rms_error_px"] == 0.0
    assert summary["max_abs_error_px"] == 0.0


def test_profile_detector_runs_on_generated_scene(tmp_path):
    dataset = tmp_path / "dataset"
    write_scene(
        dataset / "scene_a",
        count=5,
        ball_x=280,
        polarity="dark",
        gradient=0.04,
        noise=1.0,
        highlight=True,
        magnet_shadow=True,
        seed=10,
    )
    summary, rows = evaluate(dataset, detector_name="profile")
    assert summary["frames"] == 5
    assert summary["valid_rate"] == 1.0
    assert summary["max_abs_error_px"] < 10.0
    assert all(row["quality"] >= 0 for row in rows)


def test_markers_and_projective_inverse():
    positions = np.array([-100.0, 0.0, 100.0])
    for theta in (0.0, 20.0):
        pixels = project_s_to_u(positions, theta_deg=theta)
        coefficients = fit_projective(positions, pixels)
        fitted = apply_projective(positions, coefficients)
        assert np.max(np.abs(fitted - pixels)) < 0.05
        recovered = inverse_u_to_s(pixels, theta_deg=theta)
        assert np.max(np.abs(recovered - positions)) < 0.05
    _, truth = generate_scene(theta_deg=20.0, ball_s_mm=25.0)
    assert len(truth["markers"]) == 3
    assert all("s_mm" in marker and "u_px" in marker for marker in truth["markers"])
    for theta in (0.0, 10.0, 20.0):
        pixels = project_s_to_u(np.array([-135.0, 135.0]), theta_deg=theta)
        assert np.all((pixels >= 0.0) & (pixels < 640.0))


def test_independent_geometry_table():
    cases = [
        (200, 3, [-0.7, -1.1, -0.2, 2.2, 5.9]),
        (200, 6, [-1.5, -2.5, -0.7, 4.0, 11.9]),
        (300, 6, [-1.0, -1.8, -0.7, 2.2, 7.2]),
        (300, 10, [-1.9, -3.5, -2.1, 2.7, 11.0]),
        (300, 20, [-4.5, -9.6, -8.8, -0.7, 16.3]),
    ]
    positions = np.array([-100.0, -50.0, 0.0, 50.0, 100.0])
    for height, theta, expected in cases:
        projected = project_s_to_u(
            positions, theta_deg=theta, height_mm=height, scale_px_per_mm=1.0, center_u=0.0
        )
        assert np.max(np.abs((projected - positions) - np.array(expected))) < 0.1


def test_antialias_and_motion_blur_are_continuous():
    centers = []
    for position in (210.0, 210.1, 210.2):
        image, _ = generate_scene(ball_x=position, highlight=False, noise=0.0)
        crop = image[170:215, 180:240].astype(float)
        weights = 150.0 - crop
        centers.append(float(np.sum(weights * np.arange(180, 240)[None, :]) /
                            np.sum(weights)))
    assert centers[0] <= centers[1] <= centers[2]
    sharp, _ = generate_scene(vel_px_per_s=0.0, exposure_us=1000.0, highlight=False, noise=0.0)
    blurred, _ = generate_scene(vel_px_per_s=100.0, exposure_us=1000.0, highlight=False, noise=0.0)
    assert not np.array_equal(sharp, blurred)


def test_hand_calculated_metrics():
    summary = summarize_errors([1.0, -1.0, 2.0, 0.0], 4, 4)
    assert summary["mean_error_px"] == 0.5
    assert summary["rms_error_px"] == np.sqrt(1.5)
    assert summary["max_abs_error_px"] == 2.0
    assert summary["p95_abs_error_px"] == pytest.approx(1.85)
