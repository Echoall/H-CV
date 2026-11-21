import numpy as np

from k230.hcv.detect_blob import BlobDetector
from tools.synth.generate import generate_scene


PARAMS = {
    "diameter_px": 20,
    "polarity": "both",
    "k_dark": 2.5,
    "k_bright": 2.5,
    "min_contrast": 8,
    "distance_lambda": 0.2,
    "max_blobs": 32,
}


def test_blob_requires_prediction_and_limits_search_to_a_small_window():
    image, _ = generate_scene(ball_s_mm=0, polarity="dark", seed=2)
    detector = BlobDetector(PARAMS)
    detector.update_image(image[166:214])
    assert not detector.found
    assert detector.diag["reason"] == "prediction_required"


def test_blob_detects_dark_ball_near_prediction():
    image, truth = generate_scene(
        ball_s_mm=0, polarity="dark", highlight=False, magnet_shadow=False, seed=3
    )
    detector = BlobDetector(PARAMS)
    detector.update_image(image[166:214], predicted_u=truth["u_px"])
    assert detector.found
    assert abs(detector.u - truth["u_px"]) < 1.5
    assert 0 <= detector.quality <= 255


def test_blob_detects_bright_ball_near_prediction():
    image, truth = generate_scene(
        ball_s_mm=0, polarity="bright", highlight=False, magnet_shadow=False, seed=4
    )
    detector = BlobDetector(dict(PARAMS, polarity="bright"))
    detector.update_image(image[166:214], predicted_u=truth["u_px"])
    assert detector.found
    assert abs(detector.u - truth["u_px"]) < 1.5


def test_blob_rejects_prediction_outside_image():
    image, _ = generate_scene(ball_s_mm=0, polarity="dark", seed=5)
    detector = BlobDetector(PARAMS)
    detector.update_image(np.asarray(image[166:214]), predicted_u=10000)
    assert not detector.found
