import json
import os
import sys

sys.path.append("/sdcard/hcv")
from hcv.guard import start, tick
from media.media import MediaManager
from media.sensor import Sensor

sensor = Sensor(width=1280, height=720, fps=90)
media_started = False
try:
    start(30)
    sensor.reset()
    sensor.set_framesize(width=640, height=360, chn=0)
    sensor.set_pixformat(Sensor.GRAYSCALE, chn=0)
    sensor.set_framesize(width=1280, height=720, chn=1)
    sensor.set_pixformat(Sensor.YUV420SP, chn=1)
    MediaManager.init()
    media_started = True
    sensor.run()
    for _ in range(8):
        tick()
        sensor.snapshot(chn=0, timeout=1000)
    tick()
    image = sensor.snapshot(chn=0, timeout=1000)
    image1 = sensor.snapshot(chn=1, timeout=1000)
    print("HCV_IMAGE_FORMAT=%r" % (image.format(),))
    print("HCV_IMAGE_SIZE=%d,%d" % (image.width(), image.height()))
    print("HCV_IMAGE_DIR=" + json.dumps(dir(image)))
    for name in ("copy", "to_bytes", "tobytes", "compress", "save"):
        method = getattr(image, name, None)
        print("HCV_IMAGE_METHOD_%s=%s" % (name, "1" if method is not None else "0"))
    array = image.to_numpy_ref()
    print("HCV_NUMPY_TYPE=%s" % type(array).__name__)
    print("HCV_NUMPY_DIR=" + json.dumps(dir(array)))
    print("HCV_GRAY_ARRAY_SHAPE=%r" % (array.shape,))
    print("HCV_GRAY_ARRAY_BYTES=%d" % len(array.tobytes()))
    array1 = image1.to_numpy_ref()
    print("HCV_YUV_FORMAT=%r" % (image1.format(),))
    print("HCV_YUV_ARRAY_SHAPE=%r" % (array1.shape,))
    print("HCV_YUV_ARRAY_BYTES=%d" % len(array1.tobytes()))
    for name in ("to_png", "to_jpeg", "compress", "to_bitmap"):
        method = getattr(image, name, None)
        if method is None:
            continue
        try:
            value = method()
            print("HCV_IMAGE_CALL_%s=%s,%s" % (name, type(value).__name__, len(value)))
        except Exception as error:
            print("HCV_IMAGE_CALL_%s_ERROR=%r" % (name, error))
    png = image.to_png()
    png.save("/sdcard/hcv/p1_t01_probe.png")
    print("HCV_PNG_SAVE=1")
finally:
    sensor.stop()
    if media_started:
        MediaManager.deinit()
