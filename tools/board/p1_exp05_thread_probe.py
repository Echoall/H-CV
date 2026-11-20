import struct
import time

try:
    import _thread
except ImportError:
    _thread = None

import sys
sys.path.append("/sdcard/hcv")
if "link_uart" in sys.modules:
    del sys.modules["link_uart"]
from link_uart import UART1Link


if _thread is None:
    print("HCV_THREAD_ERROR,unavailable")
else:
    link = UART1Link(115200)
    samples = [0] * 1000
    done = [False]
    frame = bytearray(19)
    struct.pack_into("<HBBhhhBh", frame, 5, 1, 1, 255, 0, 0, 0, 0, -32768)

    def writer():
        for index in range(1000):
            before = time.ticks_us()
            link.uart.write(frame)
            samples[index] = time.ticks_diff(time.ticks_us(), before)
        done[0] = True

    try:
        _thread.start_new_thread(writer, ())
        while not done[0]:
            time.ticks_us()
        samples.sort()
        print("HCV_THREAD,115200,%d,%d,%d,%d" % (
            samples[500], samples[950], samples[990], samples[-1]
        ))
    finally:
        link.close()
