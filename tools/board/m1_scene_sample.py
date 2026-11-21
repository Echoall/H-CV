"""Save the current M1 detection region for checking the test scene."""

import sys

from media.media import MediaManager
from media.sensor import Sensor

sys.path.insert(0, "/sdcard/hcv")
from hcv.config import load_config
from hcv.guard import start, tick


def run():
    start(5)
    config = load_config("/sdcard/hcv")
    mode = config["sensor"]
    channel = config["chn_detect"]
    roi = config["roi"]
    sensor = None
    media_started = False
    try:
        sensor = Sensor(width=mode["width"], height=mode["height"], fps=mode["fps"])
        sensor.reset()
        size = {"width": channel["width"], "height": channel["height"], "chn": 0}
        if channel["crop"] is not None:
            size["crop"] = channel["crop"]
        sensor.set_framesize(**size)
        sensor.set_pixformat(Sensor.GRAYSCALE, chn=0)
        MediaManager.init()
        media_started = True
        sensor.run()
        for _ in range(8):
            tick()
            sensor.snapshot(chn=0, timeout=1000)
        frame = sensor.snapshot(chn=0, timeout=1000)
        cropped = frame.crop(roi=(roi["x"], roi["y"], roi["w"], roi["h"]))
        with open("/sdcard/hcv/m1-scene.pgm", "wb") as output:
            output.write(("P5\n%d %d\n255\n" % (roi["w"], roi["h"])).encode())
            output.write(cropped.to_numpy_ref().tobytes())
    finally:
        frame = cropped = None
        if sensor is not None:
            sensor.stop()
        if media_started:
            MediaManager.deinit()


run()
