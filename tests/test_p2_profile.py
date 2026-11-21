import numpy as np
import pytest

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
}


@pytest.mark.parametrize(("polarity", "highlight", "theta", "position", "velocity"), [
    ("dark", True, 0, -100, 0),
    ("bright", True, 10, 0, 0),
    ("dark", False, 20, 100, 1200),
    ("bright", False, 20, -100, -1200),
])
def test_profile_against_synthetic_reference(polarity, highlight, theta, position, velocity):
    background, _ = generate_scene(
        ball_x=-1000, noise=0, highlight=False, magnet_shadow=False, theta_deg=theta,
    )
    detector = ProfileDetector(np.mean(background[166:214], axis=0), PARAMS)
    detector.update(np.mean(background[166:214], axis=0))
    assert not detector.found

    image, truth = generate_scene(
        ball_s_mm=position, polarity=polarity, highlight=highlight, theta_deg=theta,
        vel_px_per_s=velocity, exposure_us=3000, seed=11,
    )
    detector.update(np.mean(image[166:214], axis=0))
    assert detector.found
    assert detector.u == pytest.approx(truth["u_px"], abs=1.5)
    assert 0 <= detector.quality <= 255


@pytest.mark.parametrize("gain", [0.8, 1.0, 1.25])
@pytest.mark.parametrize("offset", [-20.0, 0.0, 20.0])
def test_profile_rejects_empty_slot_under_exposure_changes(gain, offset):
    reference_image, _ = generate_scene(
        ball_x=-1000, noise=2, highlight=False, magnet_shadow=False, seed=11
    )
    reference = np.mean(reference_image[166:214], axis=0)
    image, _ = generate_scene(
        ball_x=-1000, noise=2, highlight=False, magnet_shadow=False, seed=12
    )
    changed = np.clip(image.astype(float) * gain + offset, 0, 255)
    detector = ProfileDetector(reference, PARAMS)
    detector.update_image(changed[166:214])
    assert not detector.found


@pytest.mark.parametrize("gain", [0.8, 1.0, 1.25])
@pytest.mark.parametrize("offset", [-20.0, 0.0, 20.0])
def test_profile_ball_error_stays_below_one_pixel_under_exposure_changes(
    gain, offset
):
    reference_image, _ = generate_scene(
        ball_x=-1000, noise=2, highlight=False, magnet_shadow=False, seed=11
    )
    reference = np.mean(reference_image[166:214], axis=0)
    image, truth = generate_scene(
        ball_s_mm=-20, polarity="dark", highlight=True, seed=12
    )
    changed = np.clip(image.astype(float) * gain + offset, 0, 255)
    detector = ProfileDetector(reference, PARAMS)
    detector.update_image(changed[166:214])
    assert detector.found
    assert detector.u == pytest.approx(truth["u_px"], abs=1.0)


def test_profile_background_recovers_after_consecutive_misses():
    reference_image, _ = generate_scene(
        ball_x=-1000, noise=0, highlight=False, magnet_shadow=False
    )
    reference = np.mean(reference_image[166:214], axis=0)
    changed_empty, _ = generate_scene(
        ball_x=-1000, noise=0, highlight=False, magnet_shadow=False,
        background=180,
    )
    params = dict(PARAMS)
    params["background_recovery_frames"] = 3
    params["background_recovery_alpha"] = 1.0
    detector = ProfileDetector(reference, params)
    changed_profile = np.mean(changed_empty[166:214], axis=0)
    for _ in range(3):
        detector.update(changed_profile)
        assert not detector.found
    assert detector._misses == 0
    image, truth = generate_scene(
        ball_s_mm=20, polarity="dark", highlight=True, background=180, seed=13
    )
    detector.update_image(image[166:214])
    assert detector.found
    assert detector.u == pytest.approx(truth["u_px"], abs=1.0)


def test_profile_update_image_and_result_have_unified_shape():
    image, _ = generate_scene(
        ball_s_mm=0, polarity="bright", highlight=False, seed=9
    )
    reference_image, _ = generate_scene(
        ball_x=-1000, noise=0, highlight=False, magnet_shadow=False
    )
    detector = ProfileDetector(
        np.mean(reference_image[166:214], axis=0), PARAMS, width=640
    )
    result = detector.update_image(image[166:214], predicted_u=320).result()
    assert result[0] is True
    assert result[1] == pytest.approx(320.0, abs=1.5)
    assert isinstance(result[3], dict)


def test_profile_m1_fast_path_detects_synthetic_ball():
    reference_image, _ = generate_scene(
        ball_x=-1000, noise=0, highlight=False, magnet_shadow=False
    )
    image, truth = generate_scene(
        ball_s_mm=20, polarity="dark", highlight=True, seed=19
    )
    params = dict(PARAMS, polarity="dark", m1_fast=True)
    detector = ProfileDetector(
        np.mean(reference_image[166:214], axis=0), params, width=640
    )
    detector.update(np.mean(image[166:214], axis=0))
    assert detector.found
    assert detector.u == pytest.approx(truth["u_px"], abs=2.0)


def test_profile_m1_fast_uint8_profile_matches_float_profile():
    reference_image, _ = generate_scene(
        ball_x=-1000, noise=0, highlight=False, magnet_shadow=False
    )
    image, truth = generate_scene(
        ball_s_mm=20, polarity="dark", highlight=True, seed=19
    )
    params = dict(PARAMS, polarity="dark", m1_fast=True)
    reference = np.mean(reference_image[166:214], axis=0)
    profile = np.mean(image[166:214], axis=0)
    regular = ProfileDetector(reference, params, width=640)
    byte_path = ProfileDetector(reference, params, width=640)
    regular.update(profile, predicted_u=truth["u_px"])
    byte_path.update(profile.astype(np.uint8), predicted_u=truth["u_px"])
    assert byte_path.found and regular.found
    assert byte_path.u == regular.u
    assert byte_path.quality == regular.quality


@pytest.mark.parametrize("kind", ["glare", "local_shadow"])
@pytest.mark.parametrize("width", [12, 40, 60])
@pytest.mark.parametrize("strength", [25, 40, 65])
def test_profile_rejects_local_empty_slot_interference(kind, width, strength):
    reference_image, _ = generate_scene(
        ball_x=-1000, noise=0, highlight=False, magnet_shadow=False
    )
    reference = np.mean(reference_image[166:214], axis=0)
    kwargs = {
        kind: {"x_px": 416, "width_px": width, "strength": strength}
    }
    image, _ = generate_scene(
        ball_x=-1000,
        noise=2,
        highlight=False,
        magnet_shadow=False,
        seed=17,
        **kwargs,
    )
    detector = ProfileDetector(reference, PARAMS)
    detector.update_image(image[166:214])
    assert not detector.found


@pytest.mark.parametrize("kind", ["glare", "local_shadow"])
def test_profile_keeps_ball_quality_with_remote_interference(kind):
    reference_image, _ = generate_scene(
        ball_x=-1000, noise=0, highlight=False, magnet_shadow=False
    )
    reference = np.mean(reference_image[166:214], axis=0)
    image, truth = generate_scene(
        ball_s_mm=60,
        polarity="dark",
        highlight=True,
        noise=2,
        seed=11,
        **{kind: {"x_px": 250, "width_px": 12, "strength": 25}},
    )
    detector = ProfileDetector(reference, PARAMS)
    detector.update_image(image[166:214])
    assert detector.found
    assert detector.u == pytest.approx(truth["u_px"], abs=1.0)


@pytest.mark.parametrize(
    ("ball_polarity", "opposite_interference"),
    [
        ("bright", "local_shadow"),
        ("dark", "glare"),
    ],
)
def test_profile_calibrated_polarity_ignores_opposite_remote_shape(
    ball_polarity, opposite_interference
):
    reference_image, _ = generate_scene(
        ball_x=-1000, noise=0, highlight=False, magnet_shadow=False
    )
    reference = np.mean(reference_image[166:214], axis=0)
    image, truth = generate_scene(
        ball_s_mm=60,
        polarity=ball_polarity,
        highlight=True,
        noise=2,
        seed=7,
        **{
            opposite_interference: {
                "x_px": 500,
                "width_px": 40,
                "strength": 60,
            }
        },
    )
    detector = ProfileDetector(
        reference,
        dict(PARAMS, polarity=ball_polarity),
    )
    detector.update_image(image[166:214])
    assert detector.found
    assert detector.u == pytest.approx(truth["u_px"], abs=1.0)


@pytest.mark.parametrize("kind", ["glare", "local_shadow"])
@pytest.mark.parametrize("polarity", ["dark", "bright"])
def test_profile_keeps_ball_when_remote_interference_is_84_pixels_away(
    kind, polarity
):
    reference_image, _ = generate_scene(
        ball_x=-1000, noise=0, highlight=False, magnet_shadow=False
    )
    reference = np.mean(reference_image[166:214], axis=0)
    image, truth = generate_scene(
        ball_s_mm=60,
        polarity=polarity,
        highlight=True,
        magnet_shadow=True,
        noise=2,
        seed=7,
        **{kind: {"x_px": 500, "width_px": 40, "strength": 60}},
    )
    detector = ProfileDetector(reference, dict(PARAMS, polarity=polarity))
    detector.update_image(image[166:214])
    assert detector.found
    assert detector.u == pytest.approx(truth["u_px"], abs=1.0)
