import os
import sys
if hasattr(os, "EXITPOINT_ENABLE"):
    os.exitpoint(os.EXITPOINT_ENABLE)
sys.path.insert(0, "/sdcard/hcv")
from hcv.detect_profile import ProfileDetector
from hcv.geometry import compensate
from hcv.markers import MarkerDetector

PARAMS = {
    "diameter_px": 8,
    "gate_px": 8,
    "snr_min": 2,
    "snr_good": 8,
    "noise_floor": 0.5,
    "background_alpha": 0.05,
}

def profile_case(width, height, background, polarity, ball):
    image = [[float(background) for _ in range(width)] for _ in range(height)]
    if ball is not None:
        x0, x1, y0, y1, value = ball
        for row in range(y0, y1):
            for column in range(x0, x1):
                image[row][column] = float(value)
    params = dict(PARAMS)
    params["polarity"] = polarity
    detector = ProfileDetector([float(background)] * width, params)
    return detector.update_image(image).result()

def marker_image(width, height, row0, row1, centers, value):
    image = [[100 for _ in range(width)] for _ in range(height)]
    for center in centers:
        for row in range(row0, row1):
            for column in range(center - 3, center + 4):
                image[row][column] = value
    return image

def close(actual, expected, tolerance=0.05):
    return abs(float(actual) - float(expected)) <= tolerance

results = {}
result = profile_case(64, 16, 100, "dark", (28, 36, 4, 12, 40))
assert result[0] and close(result[1], 31.5)
results["profile_dark_center"] = result[:3]
result = profile_case(64, 16, 80, "bright", (12, 20, 4, 12, 180))
assert result[0] and close(result[1], 15.5)
results["profile_bright_left"] = result[:3]
result = profile_case(64, 16, 100, "both", None)
assert not result[0] and result[1] == 0.0
results["profile_flat"] = result[:3]

image = [[100 for _ in range(96)] for _ in range(32)]
for row in range(10, 17):
    for start in (15, 45, 75):
        for column in range(start, start + 7):
            image[row][column] = 30
result = MarkerDetector(
    [18.0, 48.0, 78.0],
    {"marker_v_px": 13, "marker_width_px": 10, "marker_min_depth": 2, "polarity": "dark"},
).update_image(image).result()
assert result["count"] == 3 and result["valid"] == [True, True, True]
assert all(close(actual, expected) for actual, expected in zip(result["positions"], [18, 48, 78]))
results["markers_overlap"] = result

image = marker_image(640, 360, 145, 152, [144, 320, 496], 220)
result = MarkerDetector(
    [144.0, 320.0, 496.0],
    {"marker_v_px": 148, "marker_width_px": 7, "marker_min_depth": 2, "polarity": "bright"},
).update_image(image).result()
assert result["count"] == 3 and result["valid"] == [True, True, True]
assert all(close(actual, expected) for actual, expected in zip(result["positions"], [144, 320, 496]))
results["markers_realistic"] = result

mapped, mode, _ = compensate(45, (18, 48, 78), (20, 50, 80))
assert mode == "FULL" and close(mapped, 47)
results["geometry_full"] = [mapped, mode]
mapped, mode, _ = compensate(45, (18, None, None), (20, 50, 80))
assert mapped is None and mode == "NONE"
results["geometry_missing"] = [mapped, mode]
print("HCV_GOLDEN_BOARD", results)
