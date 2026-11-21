"""Take one composition-check picture without initializing the LCD."""
import os
import time

from media.media import MediaManager
from media.sensor import Sensor


sensor = None
media = False
os.exitpoint(os.EXITPOINT_ENABLE)
try:
    from machine import WDT
    from media.media import MediaManager
    from media.sensor import Sensor
    import os, time
    wdt = WDT(1, 15)
    sensor = Sensor(width=1280, height=720, fps=90)
    sensor.reset()
    sensor.set_framesize(width=1280, height=720)
    sensor.set_pixformat(Sensor.GRAYSCALE)
    MediaManager.init()
    sensor.run()
    for cue in range(5):
        print("HCV_CUE_LOW", cue)
        time.sleep_ms(700)
    print("HCV_CUE_HIGH")
    for index in range(3):
        os.exitpoint()
        wdt.feed()
        frame = sensor.snapshot(chn=0, timeout=1000)
        jpeg = bytes(frame.compress(quality=75))
        with open("/sdcard/hcv/p0_exp00/exp03-25mm-%d.jpg" % (index + 1), "wb") as output:
            output.write(jpeg)
        print("HCV_EXP03_SAVED", index + 1, len(jpeg))
        time.sleep_ms(400)
finally:
    try:
        if sensor is not None:
            sensor.stop()
    finally:
        if media:
            MediaManager.deinit()
