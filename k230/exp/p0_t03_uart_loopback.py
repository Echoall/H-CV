"""Run the P0-T03 voltage or loopback step with an operator present."""

import random
import sys
import time

sys.path.insert(0, "/sdcard/hcv")
import guard
from link_uart import UART1Link


BAUDS = (115200, 460800, 921600)
FRAMES = 10000
BATCH = 100
MAX_LENGTH = 64


def receive(uart, view, size):
    count = 0
    until = time.ticks_add(time.ticks_ms(), 25)
    while count < size and time.ticks_diff(until, time.ticks_ms()) > 0:
        guard.tick()
        received = uart.readinto(view[count:size])
        if received:
            count += received
    return count


def loopback(baud, count):
    link = UART1Link(baud)
    seed = 0x484356 + baud
    random.seed(seed)
    tx = bytearray(MAX_LENGTH)
    rx = bytearray(MAX_LENGTH)
    tx_view = memoryview(tx)
    rx_view = memoryview(rx)
    sent = received = mismatched = timed_out = short_writes = consecutive = 0
    print("HCV_UART_START", baud, count, seed)
    try:
        if link.uart.any():
            link.uart.read(link.uart.any())
        for index in range(1, count + 1):
            guard.tick()
            size = 1 + random.getrandbits(6)
            for pos in range(size):
                guard.tick()
                tx[pos] = random.getrandbits(8)
            written = link.uart.write(tx_view[:size])
            actual = receive(link.uart, rx_view, size)
            sent += written or 0
            received += actual
            write_failed = written != size
            read_short = actual != size
            different = not read_short and tx[:size] != rx[:size]
            short_writes += int(write_failed)
            timed_out += int(read_short)
            mismatched += int(different)
            consecutive = consecutive + 1 if write_failed or read_short or different else 0
            if index % BATCH == 0 or index == count or consecutive >= 20:
                print("HCV_UART,%d,%d,%d,%d,%d,%d,%d" %
                      (baud, index, sent, received, mismatched, timed_out, short_writes))
            if consecutive >= 20:
                raise RuntimeError("20 consecutive failed frames; check loopback wiring")
    finally:
        link.close()


def voltage():
    guard.start(95)
    link = UART1Link(115200)
    try:
        print("HCV_UART_TX_READY: measure physical pin 8 relative to GND pin 20, no jumper.")
        until = time.ticks_add(time.ticks_ms(), 90_000)
        while time.ticks_diff(until, time.ticks_ms()) > 0:
            guard.tick()
            time.sleep_ms(100)
    finally:
        link.close()


def run(mode):
    if mode == "voltage":
        voltage()
    elif mode == "loopback":
        guard.start(180)
        for baud in BAUDS:
            guard.tick()
            loopback(baud, FRAMES)
    else:
        raise ValueError("choose voltage or loopback explicitly at launch")


if __name__ == "__main__":
    run(globals().get("HCV_UART_MODE"))
