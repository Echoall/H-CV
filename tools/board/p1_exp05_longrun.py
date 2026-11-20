import gc
import struct
import sys
import time
from array import array

import ulab.numpy as np

sys.path.append("/sdcard/hcv")
if "link_uart" in sys.modules:
    del sys.modules["link_uart"]
if "geometry" in sys.modules:
    del sys.modules["geometry"]
from geometry import apply_projective, fit_projective
from guard import start as guard_start
from guard import tick as guard_tick
from link_uart import UART1Link
from media.media import MediaManager
from media.sensor import Sensor


COUNT = 10000
ROI = (0, 148, 640, 48)


def main():
    sensor = Sensor(width=1280, height=720, fps=90)
    media_started = False
    uart_link = None
    durations = array("I", [0] * COUNT)
    try:
        guard_start(240)
        sensor.reset()
        sensor.set_framesize(width=640, height=360, chn=0)
        sensor.set_pixformat(Sensor.GRAYSCALE, chn=0)
        MediaManager.init()
        media_started = True
        sensor.run()
        for _ in range(8):
            guard_tick()
            sensor.snapshot(chn=0, timeout=1000)
        uart_link = UART1Link(115200)
        tx_buffer = bytearray(19)
        struct.pack_into("<HBBhhhBh", tx_buffer, 5, 1, 1, 255, 0, 0, 0, 0, -32768)
        coefficients = fit_projective((-110.0, 0.0, 110.0), (145.0, 320.0, 495.0))
        gc.collect()
        free_before = gc.mem_free()
        gc.disable()
        manual_gc = 0
        for index in range(COUNT):
            guard_tick()
            if index % 100 == 0:
                gc.collect()
                manual_gc += 1
            before = time.ticks_us()
            image = sensor.snapshot(chn=0, timeout=1000)
            ref = image.to_numpy_ref()
            roi = ref[ROI[1]:ROI[1] + ROI[3], :]
            profile = np.mean(roi, axis=0)
            filtered = np.convolve(profile, np.ones(3) / 3.0)[1:-1]
            np.argmax(filtered)
            apply_projective(25.0, coefficients)
            struct.pack_into("<HBBhhhBh", tx_buffer, 5, index & 0xFFFF, 1, 255, 0, 0, 0, 0, -32768)
            uart_link.uart.write(tx_buffer)
            durations[index] = time.ticks_diff(time.ticks_us(), before)
        gc.enable()
        gc.collect()
        free_after = gc.mem_free()
        with open("/sdcard/hcv/exp05-longrun.csv", "w") as stream:
            stream.write("frame,duration_us\n")
            for index in range(COUNT):
                stream.write("%d,%d\n" % (index, durations[index]))
        over_period = sum(1 for value in durations if value > 11111)
        print("HCV_LONGRUN,count=%d,max_us=%d,over_11111=%d,manual_gc=%d,free_before=%d,free_after=%d" %
              (COUNT, max(durations), over_period, manual_gc, free_before, free_after))
    finally:
        gc.enable()
        if uart_link is not None:
            uart_link.close()
        sensor.stop()
        if media_started:
            MediaManager.deinit()


if __name__ == "__main__":
    main()
