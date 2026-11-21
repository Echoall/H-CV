"""Compare the CanMV column means with exact column sums on camera frames."""

import gc
import sys
import time

from image import ALLOC_REF
from media.media import MediaManager
from media.sensor import Sensor
from ulab import numpy as np

sys.path.insert(0, "/sdcard/hcv")
from hcv.config import load_config
from hcv.guard import start, tick


def run():
    start(5)
    config = load_config("/sdcard/hcv")
    mode = config["sensor"]
    channel = config["chn_detect"]
    roi = config["roi"]
    x, y, width, height = roi["x"], roi["y"], roi["w"], roi["h"]
    assert 0 < height <= 65535 // 255
    bounds = (x, y, width, height)
    pixels = bytearray(width * height)
    address = None
    sensor = None
    media_started = False
    frame = cropped = pooled = row = source = sums = None
    try:
        sensor = Sensor(width=mode["width"], height=mode["height"], fps=mode["fps"])
        sensor.reset()
        sensor.set_framesize(width=channel["width"], height=channel["height"], chn=0)
        sensor.set_pixformat(Sensor.GRAYSCALE, chn=0)
        MediaManager.init()
        media_started = True
        sensor.run()
        for index in range(10):
            tick()
            frame = sensor.snapshot(chn=0, timeout=1000)
            gc.collect()
            before = gc.mem_free()
            started = time.ticks_us()
            cropped = frame.crop(roi=bounds, alloc=ALLOC_REF, data=pixels)
            pooled = cropped.mean_pool(1, height)
            row = pooled.to_numpy_ref()[0, :]
            elapsed = time.ticks_diff(time.ticks_us(), started)
            allocated = before - gc.mem_free()
            source = frame.to_numpy_ref()[y:y + height, x:x + width]
            sums = np.sum(np.array(source, dtype=np.uint16), axis=0)
            assert row.shape == (width,)
            mismatches = 0
            for col in range(width):
                actual = int(row[col])
                expected = int(sums[col]) // height
                if actual != expected:
                    if not mismatches:
                        print(
                            "CAMERA_POOL_DIFFERENCE,frame=%d,col=%d,pool=%d,sum=%d,expected=%d"
                            % (index, col, actual, sums[col], expected)
                        )
                    mismatches += 1
            print(
                "CAMERA_POOL,frame=%d,columns=%d,mismatches=%d,us=%d,"
                "gc_alloc=%d,mmz_free=%d"
                % (index, width, mismatches, elapsed, allocated, gc.sys_mmz()[2])
            )
            if address is None:
                address = pooled.virtaddr()
            assert pooled.virtaddr() == address
            assert mismatches == 0
            frame = cropped = pooled = row = source = sums = None
    finally:
        frame = cropped = pooled = row = source = sums = None
        if sensor is not None:
            sensor.stop()
        if media_started:
            MediaManager.deinit()


run()
