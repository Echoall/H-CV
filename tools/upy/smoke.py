"""Small smoke test executed by the MicroPython + ulab CI job."""

import ulab.numpy as np

from hcv.detect_profile import ProfileDetector
from hcv.geometry import compensate
from hcv.markers import MarkerDetector


def main():
    params = {
        "diameter_px": 8,
        "gate_px": 8,
        "snr_min": 2,
        "snr_good": 8,
        "noise_floor": 0.5,
        "background_alpha": 0.05,
        "polarity": "dark",
    }
    background = np.ones(64) * 100
    image = np.ones((16, 64)) * 100
    image[:, 28:36] = 40
    detector = ProfileDetector(background, params)
    detector.update_image(image)
    assert detector.found
    assert abs(detector.u - 31.5) < 1.0

    marker_image = np.ones((32, 96)) * 100
    marker_image[10:17, 15:22] = 30
    marker_image[10:17, 45:52] = 30
    marker_image[10:17, 75:82] = 30
    markers = MarkerDetector(
        [18.0, 48.0, 78.0],
        {
            "marker_v_px": 13,
            "marker_width_px": 7,
            "marker_min_depth": 8,
            "polarity": "dark",
        },
    )
    assert markers.update_image(marker_image).result()["count"] == 3
    mapped, mode, _ = compensate(45, (18, 48, 78), (20, 50, 80))
    assert mode == "FULL"
    assert abs(mapped - 47) < 0.01
    assert compensate(45, (18, None, None), (20, 50, 80))[0] is None


main()
