import struct
import sys
import time

sys.path.append("/sdcard/hcv")
if "link_uart" in sys.modules:
    del sys.modules["link_uart"]
from link_uart import UART1Link


def measure(baud, txbuf=None):
    link = None
    try:
        link = UART1Link(baud, txbuf=txbuf)
        frame = bytearray(19)
        struct.pack_into("<HBBhhhBh", frame, 5, 1, 1, 255, 0, 0, 0, 0, -32768)
        samples = []
        for _ in range(1000):
            before = time.ticks_us()
            link.uart.write(frame)
            samples.append(time.ticks_diff(time.ticks_us(), before))
        samples.sort()
        print("HCV_UART,%d,%r,%d,%d,%d,%d" % (
            baud, txbuf, samples[500], samples[950], samples[990], samples[-1]
        ))
    except Exception as error:
        print("HCV_UART_ERROR,%d,%r,%r" % (baud, txbuf, error))
    finally:
        if link is not None:
            link.close()


for baud in (115200, 460800, 921600):
    measure(baud)
measure(115200, txbuf=256)
