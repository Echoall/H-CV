import sys
import time

import ulab.numpy as np
from media.media import MediaManager
from media.sensor import Sensor

sys.path.append("/sdcard/hcv")
from guard import start, tick


def measure(ref):
    samples = []
    for _ in range(1000):
        tick()
        before = time.ticks_us()
        np.mean(ref, axis=0)
        samples.append(time.ticks_diff(time.ticks_us(), before))
    samples.sort()
    return samples[500], samples[950], samples[990], samples[-1]


sensor = Sensor(width=1280, height=720, fps=90)
media_started = False
try:
    start(30)
    sensor.reset()
    sensor.set_framesize(width=640, height=360, chn=0)
    sensor.set_pixformat(Sensor.GRAYSCALE, chn=0)
    MediaManager.init()
    media_started = True
    sensor.run()
    for _ in range(8):
        tick()
        sensor.snapshot(chn=0, timeout=1000)
    image = sensor.snapshot(chn=0, timeout=1000)
    ref = image.to_numpy_ref()[148:196, :].copy()
    del image
    print("HCV_CONTENTION,sensor_on,%d,%d,%d,%d" % measure(ref))
    sensor.stop()
    print("HCV_CONTENTION,sensor_off,%d,%d,%d,%d" % measure(ref))
finally:
    if media_started:
        MediaManager.deinit()
