"""Record five manually triggered light cycles on the validated P0 camera mode."""

import sys
import time

sys.path.insert(0, "/sdcard/hcv")
import guard

from media.media import MediaManager
from media.sensor import Sensor


ROI = (160, 90, 320, 180)
SAMPLES_PER_STATE = 60
STATES = ("off",) + ("on", "off") * 5


def sample():
    sensor = None
    media = False
    guard.start(45)
    try:
        sensor = Sensor(width=1280, height=720, fps=90)
        sensor.reset()
        sensor.set_framesize(width=640, height=360)
        sensor.set_pixformat(Sensor.GRAYSCALE)
        media = True
        MediaManager.init()
        sensor.run()
        for _ in range(8):
            guard.tick()
            sensor.snapshot(chn=0, timeout=1000)
        beginning = time.ticks_ms()
        for phase, state in enumerate(STATES):
            guard.tick()
            phase_start = time.ticks_ms()
            print("HCV_LIGHT", phase, state, time.ticks_diff(phase_start, beginning))
            for index in range(SAMPLES_PER_STATE):
                guard.tick()
                now = time.ticks_ms()
                due = time.ticks_add(phase_start, 50 * index)
                remaining = time.ticks_diff(due, now)
                if remaining > 0:
                    time.sleep_ms(remaining)
                image = sensor.snapshot(chn=0, timeout=1000)
                received = time.ticks_ms()
                average = image.get_statistics(roi=ROI).mean()
                print("HCV_LUMA,%d,%d,%s" % (phase, time.ticks_diff(received, beginning), average))
            wait = time.ticks_diff(time.ticks_add(phase_start, 3000), time.ticks_ms())
            if wait > 0:
                time.sleep_ms(wait)
    finally:
        try:
            if sensor is not None:
                sensor.stop()
        finally:
            if media:
                MediaManager.deinit()


if __name__ == "__main__":
    sample()
