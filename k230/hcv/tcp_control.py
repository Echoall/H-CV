"""Send a bounded synthetic TCP load before the media session."""

import socket
import time

from .guard import start, tick


def send_control(address, port):
    start(120)
    block = b"K" * 19000
    listener = socket.socket()
    try:
        listener.settimeout(45)
        listener.bind((address, port))
        listener.listen(1)
        print("HCV_TCP_CONTROL_READY")
        client, _ = listener.accept()
        try:
            client.settimeout(3)
            began = time.ticks_ms()
            for index in range(600):
                tick()
                client.sendall(block)
                next_send = time.ticks_add(began, (index + 1) * 100)
                delay = time.ticks_diff(next_send, time.ticks_ms())
                if delay > 0:
                    time.sleep_ms(delay)
            # The receiver half-closes first so the board can reuse the same port.
            if client.recv(1) != b"":
                raise OSError("unexpected TCP control response")
            print("HCV_TCP_CONTROL_SENT,bytes=%d,elapsed_ms=%d" % (
                600 * len(block), time.ticks_diff(time.ticks_ms(), began),
            ))
        finally:
            client.close()
    finally:
        listener.close()
