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
