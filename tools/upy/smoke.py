"""Small smoke test executed by the MicroPython + ulab CI job."""

import ulab
import json

np = ulab.numpy

from hcv.detect_profile import ProfileDetector
from hcv.geometry import compensate
from hcv.markers import MarkerDetector


def main():
    golden = json.load(open("tools/upy/golden.json"))
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
    image[4:12, 28:36] = 40
    detector = ProfileDetector(background, params)
    detector.update_image(image)
    assert detector.found == golden["profile"]["found"]
    assert abs(detector.u - golden["profile"]["u"]) <= 0.05

    marker_image = [[100 for _ in range(96)] for _ in range(32)]
    for row in range(10, 17):
        for column in range(15, 22):
            marker_image[row][column] = 30
        for column in range(45, 52):
            marker_image[row][column] = 30
        for column in range(75, 82):
            marker_image[row][column] = 30
    markers = MarkerDetector(
        [18.0, 48.0, 78.0],
        {
            "marker_v_px": 13,
            "marker_width_px": 10,
            "marker_min_depth": 2,
            "polarity": "dark",
        },
    )
    marker_result = markers.update_image(marker_image).result()
    print(marker_result)
    assert marker_result["count"] == golden["markers"]["count"]
    assert marker_result["valid"] == golden["markers"]["valid"]
    mapped, mode, _ = compensate(45, (18, 48, 78), (20, 50, 80))
    assert mode == golden["geometry"]["mode"]
    assert abs(mapped - golden["geometry"]["mapped"]) <= 0.05
    assert (
        compensate(45, (18, None, None), (20, 50, 80))[0] is None
    ) == golden["geometry"]["missing_is_invalid"]


main()
