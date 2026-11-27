"""Capture a full grayscale ruler image and a 60-frame empty-slot reference."""

import json
import sys

from media.media import MediaManager
from media.sensor import Sensor

sys.path.insert(0, "/sdcard/hcv")
for name in list(sys.modules):
    if name == "main_hcv" or name == "hcv" or name.startswith("hcv."):
        del sys.modules[name]

from hcv.config import calibration_fingerprint, load_config
from hcv.detect_profile import ProfileDetector
from hcv.guard import start, tick
from main_hcv import _write_column_profile, configure_detector_sensor


def sample_ball(sensor, bounds, pixels, profile, detector, fingerprint):
    from ulab import numpy as np

    count = 120
    positions = [None] * count
    columns = np.frombuffer(profile, dtype=np.uint8)
    print("HCV_M2_CAPTURE_CUE")
    for index in range(count):
        tick()
        frame = sensor.snapshot(chn=0, timeout=1000)
        _write_column_profile(frame, bounds, pixels, profile)
        detector.update(columns, roi_pixels=pixels)
        if detector.found:
            positions[index] = float(detector.u) + bounds[0]
    with open("/sdcard/hcv/m2-ball-pixels.json", "w") as stream:
        json.dump({"fingerprint": fingerprint, "u_px": positions}, stream)


def capture(preview_only=False, rehearsal_roi=None, cue=False, ball_samples=False):
    start(15 if ball_samples else 8)
    config = load_config("/sdcard/hcv")
    rehearsal = preview_only or rehearsal_roi is not None
    if rehearsal:
        config["chn_detect"]["crop"] = None
    if rehearsal_roi is not None:
        config["roi"] = rehearsal_roi
    mode = config["sensor"]
    roi = config["roi"]
    width, height = roi["w"], roi["h"]
    bounds = (roi["x"], roi["y"], width, height)
    if not preview_only:
        pixels = bytearray(width * height)
        profile = bytearray(width)
    if ball_samples:
        fingerprint = calibration_fingerprint(config)
        with open("/sdcard/hcv/m2-reference.json", "r") as stream:
            reference = json.load(stream)
        if reference["fingerprint"] != fingerprint or reference["samples"] < 60:
            raise ValueError("ball sampling needs a fresh empty-slot reference")
        detector = ProfileDetector(reference["bg_profile"], config["detector"]["params"], width=width)
    elif not preview_only:
        totals = [0] * width
    count = 60
    sensor = None
    media_started = False
    frame = None
    try:
        sensor = Sensor(width=mode["width"], height=mode["height"], fps=mode["fps"])
        configure_detector_sensor(sensor, config)
        MediaManager.init()
        media_started = True
        sensor.run()
        for _ in range(8):
            tick()
            frame = sensor.snapshot(chn=0, timeout=1000)
        if ball_samples:
            frame = None
            sample_ball(sensor, bounds, pixels, profile, detector, fingerprint)
            return
        if cue:
            print("HCV_M2_CAPTURE_CUE")
            tick()
            frame = sensor.snapshot(chn=0, timeout=1000)
        image_width = config["chn_detect"]["width"]
        image_height = config["chn_detect"]["height"]
        if preview_only:
            image_path = "/sdcard/hcv/m2-rehearsal-preview.pgm"
        elif rehearsal:
            image_path = "/sdcard/hcv/m2-rehearsal-ruler.pgm"
        else:
            image_path = "/sdcard/hcv/m2-ruler.pgm"
        with open(image_path, "wb") as stream:
            stream.write(("P5\n%d %d\n255\n" % (image_width, image_height)).encode())
            stream.write(frame.to_numpy_ref().tobytes())
        if preview_only:
            print("HCV_M2_PREVIEW,width=%d,height=%d" % (image_width, image_height))
            return
        for _ in range(count):
            tick()
            frame = sensor.snapshot(chn=0, timeout=1000)
            _write_column_profile(frame, bounds, pixels, profile)
            for col in range(width):
                totals[col] += profile[col]
        reference_path = (
            "/sdcard/hcv/m2-rehearsal-reference.json" if rehearsal else "/sdcard/hcv/m2-reference.json"
        )
        with open(reference_path, "w") as stream:
            json.dump({
                "fingerprint": calibration_fingerprint(config),
                "config": {
                    "sensor": mode,
                    "chn_detect": config["chn_detect"],
                    "roi": roi,
                    "detector": {"name": config["detector"]["name"]},
                },
                "samples": count,
                "bg_profile": [(value + count // 2) // count for value in totals],
            }, stream)
        print("HCV_M2_REFERENCE,frames=%d" % count)
    finally:
        frame = None
        try:
            if sensor is not None:
                sensor.stop()
        finally:
            if media_started:
                MediaManager.deinit()


capture(
    globals().pop("HCV_PREVIEW_ONLY", False),
    globals().pop("HCV_REHEARSAL_ROI", None),
    globals().pop("HCV_CAPTURE_CUE", False),
    globals().pop("HCV_BALL_SAMPLES", False),
)
