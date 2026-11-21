import gc
import struct
import sys
import time

import ulab.numpy as np

sys.path.append("/sdcard/hcv")
from geometry import apply_projective, fit_projective
from guard import start as guard_start
from guard import tick as guard_tick
from link_uart import UART1Link
from media.media import MediaManager
from media.sensor import Sensor


N = 1000
ROI = (0, 148, 640, 48)


def emit(name, index, elapsed):
    print("HCV_EXP05,%s,%d,%d" % (name, index, elapsed))


def measure(name, function):
    for index in range(N):
        guard_tick()
        before = time.ticks_us()
        function()
        emit(name, index, time.ticks_diff(time.ticks_us(), before))


def measure_auto_gc():
    events = 0
    for index in range(N):
        guard_tick()
        before_free = gc.mem_free()
        before = time.ticks_us()
        temporary = [0] * 640
        del temporary
        after_free = gc.mem_free()
        if after_free > before_free + 4096:
            events += 1
        emit("gc_auto_alloc", index, time.ticks_diff(time.ticks_us(), before))
    print("HCV_EXP05_META,gc_auto_events,%d" % events)


def main():
    sensor = Sensor(width=1280, height=720, fps=90)
    media_started = False
    uart_link = None
    try:
        guard_start(300)
        sensor.reset()
        sensor.set_framesize(width=640, height=360, chn=0)
        sensor.set_pixformat(Sensor.GRAYSCALE, chn=0)
        MediaManager.init()
        media_started = True
        sensor.run()
        for _ in range(8):
            guard_tick()
            sensor.snapshot(chn=0, timeout=1000)
        image = sensor.snapshot(chn=0, timeout=1000)
        ref = image.to_numpy_ref()
        roi = ref[ROI[1]:ROI[1] + ROI[3], ROI[0]:ROI[0] + ROI[2]]
        uart_link = UART1Link(115200)
        tx_buffer = bytearray(19)
        s_values = (-110.0, 0.0, 110.0)
        u_values = (145.0, 320.0, 495.0)
        coefficients = fit_projective(s_values, u_values)
        print("HCV_EXP05_META,free_before,%d" % gc.mem_free())
        print("HCV_EXP05_META,geometry_check,%.9f" %
              abs(apply_projective(0.0, coefficients) - 320.0))

        def snapshot():
            sensor.snapshot(chn=0, timeout=1000)

        def copy_roi():
            ref[ROI[1]:ROI[1] + ROI[3], ROI[0]:ROI[0] + ROI[2]].copy()

        def mean_profile():
            np.mean(image.to_numpy_ref()[ROI[1]:ROI[1] + ROI[3], :], axis=0)

        def sum_box_argmax():
            profile = np.mean(roi, axis=0)
            np.sum(profile)
            filtered = np.convolve(profile, np.ones(3) / 3.0)[1:-1]
            np.argmax(filtered)

        def blobs_dark():
            image.find_blobs([(0, 80)], roi=ROI, pixels_threshold=10,
                             area_threshold=10, merge=True, margin=2)

        def blobs_bright():
            image.find_blobs([(170, 255)], roi=ROI, pixels_threshold=10,
                             area_threshold=10, merge=True, margin=2)

        def statistics():
            image.get_statistics(roi=ROI)

        def histogram():
            image.get_histogram(roi=ROI)

        def marker_profiles():
            for center in (145, 320, 495):
                np.mean(roi[:, max(0, center - 10):center + 10], axis=0)

        def projective_lookup():
            fitted = fit_projective(s_values, u_values)
            apply_projective(25.0, fitted)

        def pack_and_write():
            struct.pack_into("<HBBhhhBh", tx_buffer, 5, 1, 1, 255, 0, 0, 0, 0, -32768)
            tx_buffer[0] = 0xA5
            tx_buffer[1] = 0x5A
            tx_buffer[2] = 0x0F
            tx_buffer[3] = 0x01
            tx_buffer[4] = 0
            uart_link.uart.write(tx_buffer)

        def gc_no_alloc():
            gc.collect()

        def gc_with_alloc():
            temporary = [0] * 640
            del temporary
            gc.collect()

        def ticks_overhead():
            time.ticks_us()

        measure("snapshot", snapshot)
        measure("copy_roi", copy_roi)
        measure("to_numpy_mean", mean_profile)
        measure("sum_box_argmax", sum_box_argmax)
        measure("find_blobs_dark", blobs_dark)
        measure("find_blobs_bright", blobs_bright)
        measure("get_statistics", statistics)
        measure("get_histogram", histogram)
        measure("marker_profiles", marker_profiles)
        measure("projective_lookup", projective_lookup)
        measure("pack_uart_write", pack_and_write)
        measure("gc_no_alloc", gc_no_alloc)
        measure("gc_with_alloc", gc_with_alloc)
        measure_auto_gc()
        measure("ticks_us", ticks_overhead)
        print("HCV_EXP05_META,free_after,%d" % gc.mem_free())
    finally:
        if uart_link is not None:
            uart_link.close()
        sensor.stop()
        if media_started:
            MediaManager.deinit()


if __name__ == "__main__":
    main()
