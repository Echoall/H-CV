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
    assert detector.u == pytest.approx(truth["u_px"], abs=3)
    assert 0 <= detector.quality <= 255
