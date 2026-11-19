"""P0-EXP01: time one bounded CanMV camera configuration."""

from array import array
import json
import os
import time

from media.media import MediaManager
from media.sensor import Sensor


def event(kind, **fields):
    fields["event"] = kind
    print("HCV_EXP01 " + json.dumps(fields))


def measure(case, frames):
    width, height, fps, out_w, out_h, second_channel, crop = case
    sensor = None
    reset_done = False
    media_attempted = False
    errors = []
    ends = array("I", [0] * frames)
    durations = array("I", [0] * frames)
    acquired = 0
    started = None
    try:
        if hasattr(os, "EXITPOINT_ENABLE"):
            os.exitpoint(os.EXITPOINT_ENABLE)
        sensor = Sensor(width=width, height=height, fps=fps)
        sensor.reset()
        reset_done = True
        mode = sensor._dev_attr.sensor_info
        actual = [mode.width, mode.height, mode.fps]
        name = sensor.sensor_name
        if isinstance(name, bytes):
            name = name.decode()
        event("mode", requested=[width, height, fps], actual=actual,
              sensor=name, sensor_type=sensor.get_type(), sensor_id=sensor.get_id())
        equivalent = actual[:2] == [width, height] and abs(actual[2] - fps) <= 1
        if not equivalent:
            event("unsupported", reason="sensor selected a different physical mode")
            return

        window = None
        if crop:
            x = ((actual[0] - out_w) // 2) & ~1
            y = ((actual[1] - out_h) // 2) & ~1
            window = (x, y, out_w, out_h)
        sensor.set_framesize(width=out_w, height=out_h, chn=0, crop=window)
        sensor.set_pixformat(Sensor.GRAYSCALE, chn=0)
        if second_channel:
            sensor.set_framesize(width=1280, height=720, chn=1)
            sensor.set_pixformat(Sensor.YUV420SP, chn=1)
        media_attempted = True
        MediaManager.init()
        sensor.run()
        for _ in range(8):
            if hasattr(os, "exitpoint"):
                os.exitpoint()
            sensor.snapshot(chn=0, timeout=1000)
        started = time.ticks_us()
        while acquired < frames:
            if hasattr(os, "exitpoint"):
                os.exitpoint()
            before = time.ticks_us()
            if time.ticks_diff(before, started) > 90_000_000:
                raise RuntimeError("90-second measurement budget exhausted")
            image = sensor.snapshot(chn=0, timeout=1000)
            after = time.ticks_us()
            ends[acquired] = time.ticks_diff(after, started)
            durations[acquired] = time.ticks_diff(after, before)
            acquired += 1
        event("image", width=image.width(), height=image.height(), format=image.format(),
              gray_constant=Sensor.GRAYSCALE, crop=window,
              channel1=list(sensor.get_framesize(1)) if second_channel else None)
    except Exception as error:
        errors.append(repr(error))
        event("error", detail=repr(error))
    finally:
        if reset_done:
            try:
                sensor.stop()
            except Exception as error:
                errors.append(repr(error))
                event("cleanup_error", stage="sensor.stop", detail=repr(error))
        if media_attempted:
            try:
                MediaManager.deinit()
            except Exception as error:
                errors.append(repr(error))
                event("cleanup_error", stage="MediaManager.deinit", detail=repr(error))
        for index in range(acquired):
            print("HCV_FRAME,%d,%d,%d" % (index, ends[index], durations[index]))
        event("done", expected=frames, acquired=acquired, failures=len(errors))


if __name__ == "__main__":
    measure(HCV_CASE, HCV_FRAMES)
