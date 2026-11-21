import numpy as np
import pytest

from k230.hcv.geometry import (
    apply_projective,
    compensation_quality,
    compensate,
    fit_projective,
    fit_similarity,
    interpolate_lut,
)
from k230.hcv.markers import MarkerDetector
from tools.synth.generate import project_s_to_u, generate_scene


def test_marker_detector_finds_three_synthetic_markers():
    image, truth = generate_scene(
        ball_x=-1000,
        theta_deg=20,
        noise=0,
        highlight=False,
        magnet_shadow=False,
    )
    expected = [marker["u_px"] for marker in truth["markers"]]
    detector = MarkerDetector(
        expected,
        {
            "search_px": 12,
            "marker_v_px": truth["markers"][0]["v_px"],
            "marker_width_px": 7,
            "marker_min_depth": 8,
            "polarity": "dark",
        },
    )
    result = detector.update_image(image).result()
    assert result["count"] == 3
    assert all(
        measured == pytest.approx(reference, abs=0.5)
        for measured, reference in zip(result["positions"], expected)
    )


def test_marker_capture_handles_twenty_degree_start_without_prediction():
    reference_image, reference_truth = generate_scene(
        ball_x=-1000,
        theta_deg=0,
        noise=0,
        highlight=False,
        magnet_shadow=False,
    )
    image, truth = generate_scene(
        ball_x=-1000,
        theta_deg=20,
        noise=2,
        highlight=False,
        magnet_shadow=False,
        seed=7,
    )
    reference = [marker["u_px"] for marker in reference_truth["markers"]]
    detector = MarkerDetector(
        reference,
        {
            "marker_v_px": truth["markers"][0]["v_px"],
            "marker_width_px": 7,
            "marker_min_depth": 8,
            "polarity": "dark",
        },
    )
    result = detector.update_image(image).result()
    assert result["count"] == 3
    assert max(
        abs(measured - expected)
        for measured, expected in zip(
            result["positions"], [marker["u_px"] for marker in truth["markers"]]
        )
    ) < 0.3


def test_marker_detector_tracks_continuous_tilt_without_losing_lock():
    _, reference_truth = generate_scene(
        ball_x=-1000,
        theta_deg=0,
        noise=0,
        highlight=False,
        magnet_shadow=False,
    )
    reference = [marker["u_px"] for marker in reference_truth["markers"]]
    detector = MarkerDetector(
        reference,
        {
            "marker_v_px": reference_truth["markers"][0]["v_px"],
            "marker_width_px": 7,
            "marker_min_depth": 8,
            "polarity": "dark",
        },
    )
    angles = list(np.arange(0, 20.1, 0.5))
    angles.extend(np.arange(19.5, -20.1, -0.5))
    for seed, theta in enumerate(angles):
        image, _ = generate_scene(
            ball_x=-1000,
            theta_deg=float(theta),
            noise=2,
            highlight=False,
            magnet_shadow=False,
            seed=seed,
        )
        assert detector.update_image(image).result()["count"] == 3


def test_projective_compensation_residual_is_submillimetre_at_twenty_degrees():
    marker_s = [-110.0, 0.0, 110.0]
    reference_u = project_s_to_u(marker_s, theta_deg=0)
    current_u = project_s_to_u(marker_s, theta_deg=20)
    ball_s = 25.0
    ball_current = float(project_s_to_u(ball_s, theta_deg=20))
    compensated, mode, _ = compensate(ball_current, current_u, reference_u)
    expected = float(project_s_to_u(ball_s, theta_deg=0))
    recovered_s = (compensated - 320.0) / 1.6
    expected_s = (expected - 320.0) / 1.6
    assert mode == "FULL"
    assert abs(recovered_s - expected_s) < 0.5 / 1.6


def test_two_markers_use_similarity_fallback():
    measured, mode, coefficients = compensate(
        200.0, [100.0, 300.0], [110.0, 310.0]
    )
    assert mode == "DEGRADED"
    assert measured == pytest.approx(210.0)
    assert coefficients == pytest.approx((1.0, 10.0))
    assert compensation_quality(mode) == pytest.approx(0.25)


@pytest.mark.parametrize("theta", [0.0, 10.0, 20.0])
@pytest.mark.parametrize("missing", [0, 1, 2])
def test_two_markers_with_known_hinge_geometry_stay_below_two_mm(theta, missing):
    model = {
        "height_mm": 300.0,
        "scale_px_per_mm": 1.6,
        "center_u": 320.0,
        "pivot_mm": -125.0,
        "marker_s_mm": [-110.0, 0.0, 110.0],
    }
    reference = [
        float(value)
        for value in project_s_to_u(model["marker_s_mm"], theta_deg=0)
    ]
    current = [
        float(value)
        for value in project_s_to_u(model["marker_s_mm"], theta_deg=theta)
    ]
    errors = []
    for position in (-100.0, -50.0, 0.0, 50.0, 100.0):
        measured = float(project_s_to_u(position, theta_deg=theta))
        observed = [
            value if index != missing else None
            for index, value in enumerate(current)
        ]
        compensated, mode, _ = compensate(
            measured, observed, reference, model=model
        )
        recovered = (compensated - 320.0) / 1.6
        errors.append(recovered - position)
        assert mode == "GEOMETRIC"
    assert max(abs(error) for error in errors) < 2.0
    assert compensation_quality(mode) == pytest.approx(0.75)


def test_missing_markers_use_identity_fallback():
    measured, mode, coefficients = compensate(200.0, [None], [100.0])
    assert measured is None
    assert mode == "NONE"
    assert coefficients is None
    assert compensation_quality(mode) == 0.0


def test_ill_conditioned_projective_fit_falls_back_to_similarity():
    with pytest.raises(ValueError):
        fit_projective([100.0, 100.0, 100.0], [101.0, 102.0, 103.0])
    measured, mode, _ = compensate(
        200.0, [100.0, 100.0, 300.0], [110.0, 120.0, 310.0]
    )
    assert mode == "DEGRADED"
    assert measured == pytest.approx(210.0)


def test_calibration_lookup_is_monotonic_and_linear_between_points():
    assert interpolate_lut(150, [100, 200], [-50, 50]) == pytest.approx(0)
    assert interpolate_lut(50, [100, 200], [-50, 50]) == pytest.approx(-100)
    with pytest.raises(ValueError):
        interpolate_lut(150, [100, 100], [-50, 50])
