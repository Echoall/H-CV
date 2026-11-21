import gc
import sys
import time

import ulab.numpy as np

sys.path.insert(0, "/sdcard")
sys.path.insert(1, "/sdcard/hcv")
from guard import start as guard_start
from guard import tick as guard_tick
from hcv.detect_profile import ProfileDetector
from hcv.geometry import compensate
from hcv.markers import MarkerDetector

N = 1000

def percentile(values, fraction):
    values = sorted(values)
    index = int((len(values) - 1) * fraction)
    return values[index]

def measure(name, function):
    print("HCV_P2_BEGIN,%s" % name)
    gc.collect()
    before_free = gc.mem_free()
    values = []
    for _ in range(N):
        guard_tick()
        before = time.ticks_us()
        function()
        values.append(time.ticks_diff(time.ticks_us(), before))
    after_free = gc.mem_free()
    print("HCV_P2_TIMING,%s,%d,%d,%d,%d,%d,%d,%d" % (
        name, percentile(values, 0.50), percentile(values, 0.95),
        percentile(values, 0.99), max(values), before_free, after_free,
        before_free - after_free))
    print("HCV_P2_END,%s" % name)


def main():
    guard_start(180)
    profile_image = [[100 for _ in range(640)] for _ in range(48)]
    for row in range(20, 28):
        for column in range(312, 320):
            profile_image[row][column] = 40
    profile = ProfileDetector(
        [100 for _ in range(640)],
        {
            "diameter_px": 20,
            "gate_px": 20,
            "snr_min": 6,
            "snr_good": 20,
            "noise_floor": 0.5,
            "background_alpha": 0.05,
            "polarity": "dark",
        },
    )
    marker_image = [[100 for _ in range(640)] for _ in range(48)]
    for center in (144, 320, 496):
        for row in range(20, 27):
            for column in range(center - 3, center + 4):
                marker_image[row][column] = 30
    marker_image = np.array(marker_image)
    markers = MarkerDetector(
        [144.0, 320.0, 496.0],
        {
            "marker_v_px": 23,
            "marker_width_px": 7,
            "marker_min_depth": 8,
            "marker_height_px": 4,
            "polarity": "dark",
        },
    )
    reference = (144.0, 320.0, 496.0)
    current = (144.0, 320.0, 496.0)
    model = {
        "height_mm": 300.0,
        "scale_px_per_mm": 1.6,
        "center_u": 320.0,
        "pivot_mm": -125.0,
        "marker_s_mm": [-110.0, 0.0, 110.0],
    }
    profile_line = np.ones(640) * 100.0
    for column in range(312, 320):
        profile_line[column] = 40
    measure("profile_detector", lambda: profile.update(profile_line))
    markers.update_image(marker_image)
    measure("marker_detector", lambda: markers.update_image(marker_image))
    measure(
        "geometry_full",
        lambda: compensate(416.0, current, reference),
    )
    measure(
        "geometry_two_point",
        lambda: compensate(416.0, (None, 320.0, 496.0), reference, model=model),
    )
    print("HCV_P2_DONE")

main()
