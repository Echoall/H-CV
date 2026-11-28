"""One v1.8 sensor capture and normal release. The host confirms each acquisition."""
import gc
import os


def acquire(Sensor, start_guard, tick_guard, token, stage):
    global _hcv_s0_release
    previous = globals().get("_hcv_s0_release")
    expected = (token, "first") if stage == "same_runtime" else None
    if stage not in ("first", "same_runtime", "after_reset") or previous != expected:
        raise RuntimeError("unexpected interpreter session")
    _hcv_s0_release = (token, "busy")
    start_guard(8)
    tick_guard()
    sensor = Sensor(width=1280, height=720, fps=90)
    try:
        sensor.reset()
        sensor.set_framesize(width=320, height=240, chn=0)
        sensor.set_pixformat(Sensor.GRAYSCALE, chn=0)
        sensor.run()
        tick_guard()
        image = sensor.snapshot(chn=0, timeout=1000)
        if image is None or (image.width(), image.height()) != (320, 240):
            raise RuntimeError("incomplete sensor frame")
        print("HCV_S0_FRAME,%s,%s,320,240" % (token, stage))
    finally:
        image = None
        sensor.stop()
        sensor = None
        gc.collect()
    tick_guard()
    _hcv_s0_release = (token, stage)
    print("HCV_S0_RELEASED,%s,%s" % (token, stage))


def run(token, stage):
    identity = os.uname()
    if identity.machine != "k230_canmv_hiwonder" or identity.release.split("-")[0] != "v1.8":
        raise RuntimeError("expected Hiwonder v1.8")
    from media.sensor import Sensor
    # The host includes the project guard in this raw-REPL script.
    acquire(Sensor, start, tick, token, stage)
