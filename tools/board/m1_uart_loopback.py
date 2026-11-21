import sys
import time

sys.path.insert(0, "/sdcard")
sys.path.insert(1, "/sdcard/hcv")
for name in list(sys.modules):
    if name == "hcv" or name.startswith("hcv."):
        del sys.modules[name]

from hcv.hcv.guard import start as guard_start
from hcv.hcv.guard import tick as guard_tick
from hcv.hcv.link_uart import UART1Link
from hcv.hcv.proto import MSG_BALL, RxParser, pack_ball_into


COUNT = 10000


def main():
    guard_start(45)
    link = None
    parser = RxParser()
    tx = bytearray(19)
    sent = 0
    received = 0
    expected = 0
    started = time.ticks_ms()
    try:
        link = UART1Link(115200)
        while received < COUNT:
            guard_tick()
            if sent < COUNT:
                pack_ball_into(
                    tx,
                    sent & 0xFF,
                    sent & 0xFFFF,
                    0,
                    0,
                    -32768,
                    -32768,
                    -32768,
                    0,
                    -32768,
                )
                link.uart.write(tx)
                sent += 1
            available = link.uart.any()
            if available:
                for message in parser.feed(link.uart.read(min(available, 256))):
                    if message[0] != MSG_BALL:
                        continue
                    if message[1] != expected:
                        raise RuntimeError(
                            "sequence mismatch expected %d got %d"
                            % (expected, message[1])
                        )
                    expected = (expected + 1) & 0xFF
                    received += 1
            if time.ticks_diff(time.ticks_ms(), started) > 40000:
                raise RuntimeError("loopback timeout")
        print(
            "HCV_M1_LOOPBACK,sent=%d,received=%d,crc_errors=%d,length_errors=%d"
            % (sent, received, parser.crc_errors, parser.length_errors)
        )
    finally:
        if link is not None:
            link.close()


main()
