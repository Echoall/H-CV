"""Run bounded AP throughput trials without opening the camera or encoder."""

import errno
import socket
import struct
import sys
import time


PORT = 8554
BLOCK_BYTES = 1000
TRIALS = (
    ("tcp", "board_to_pc", 25, 30),
    ("tcp", "board_to_pc", 50, 30),
    ("tcp", "board_to_pc", 100, 30),
    ("tcp", "board_to_pc", 200, 30),
    ("tcp", "pc_to_board", 100, 30),
    ("udp", "board_to_pc", 100, 30),
    ("udp", "board_to_pc", 200, 30),
    ("tcp", "board_to_pc", 300, 60),
)
DATA = b"K" * BLOCK_BYTES
WOULD_BLOCK = (errno.EAGAIN, getattr(errno, "EWOULDBLOCK", errno.EAGAIN), 10035)


if hasattr(time, "ticks_ms"):
    sys.path.insert(0, "/sdcard/hcv")
    from hcv.guard import start, tick

    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
    sleep_ms = time.sleep_ms
else:
    def ticks_ms():
        return time.monotonic_ns() // 1_000_000

    def ticks_diff(new, old):
        return new - old

    def sleep_ms(delay):
        time.sleep(delay / 1000)

    def tick():
        pass


def no_data(error):
    return bool(error.args and error.args[0] in WOULD_BLOCK)


def wait_call(action, limit_ms):
    started = ticks_ms()
    while ticks_diff(ticks_ms(), started) < limit_ms:
        tick()
        try:
            return action()
        except OSError as error:
            if not no_data(error):
                raise
        sleep_ms(10)
    raise OSError("no activity before timeout")


def transfer(sock, sending, rate, seconds, peer=None):
    """Measure one direction. A peer address selects numbered UDP datagrams."""
    blocks = rate * seconds
    started = last_activity = ticks_ms()
    transferred = largest_gap = 0
    error = None
    seen = None
    if peer is not None:
        if sending:
            datagram = bytearray(DATA)
        else:
            seen = set()
    sock.setblocking(False)
    try:
        while transferred < blocks * BLOCK_BYTES:
            tick()
            now = ticks_ms()
            elapsed = ticks_diff(now, started)
            if elapsed >= seconds * 1000 + 8000:
                raise OSError("trial deadline")
            if ticks_diff(now, last_activity) >= 4000:
                if seen is not None:
                    break
                raise OSError("no activity for 4 seconds")
            try:
                if sending:
                    index, offset = divmod(transferred, BLOCK_BYTES)
                    delay = (index + 1) * 1000 // rate - elapsed
                    if delay > 0:
                        sleep_ms(delay)
                    if peer is None:
                        n = sock.send(DATA[offset:] if offset else DATA)
                    else:
                        struct.pack_into(">I", datagram, 0, index)
                        n = sock.sendto(datagram, peer)
                    if n == 0:
                        raise OSError("socket sent zero bytes")
                elif peer is None:
                    payload = sock.recv(min(8192, blocks * BLOCK_BYTES - transferred))
                    if not payload:
                        raise OSError("premature EOF")
                    n = len(payload)
                    if payload.count(b"K") != n:
                        raise OSError("unexpected TCP payload")
                else:
                    payload, source = sock.recvfrom(BLOCK_BYTES + 1)
                    if source != peer or len(payload) != BLOCK_BYTES:
                        continue
                    sequence = struct.unpack_from(">I", payload)[0]
                    if sequence >= blocks or sequence in seen:
                        continue
                    seen.add(sequence)
                    n = BLOCK_BYTES
            except OSError as failure:
                if not no_data(failure):
                    raise
                sleep_ms(1)
                continue
            transferred += n
            now = ticks_ms()
            largest_gap = max(largest_gap, ticks_diff(now, last_activity))
            last_activity = now
    except OSError as failure:
        error = failure.args[0] if failure.args else type(failure).__name__
    return {
        "bytes": transferred, "elapsed_ms": ticks_diff(ticks_ms(), started),
        "active_ms": ticks_diff(last_activity, started), "max_activity_gap_ms": largest_gap,
        "expected_bytes": blocks * BLOCK_BYTES, "error": error,
        "missing_datagrams": blocks - len(seen) if seen is not None else None,
    }


def banner(index):
    return ("HCVLINK%d\n" % index).encode()


def serve(address, index, trial):
    protocol, direction, rate, seconds = trial
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.setblocking(False)
        listener.bind((address, PORT))
        if protocol == "tcp":
            listener.listen(1)
        print("HCV_LINK_READY,%d" % index)
        limit = 45_000 if index == 0 else 18_000
        if protocol == "udp":
            greeting, peer = wait_call(lambda: listener.recvfrom(64), limit)
            if greeting != banner(index):
                raise OSError("unexpected UDP control message")
            return transfer(listener, True, rate, seconds, peer)
        client, _ = wait_call(listener.accept, limit)
        try:
            client.setblocking(False)
            greeting = banner(index)
            while greeting:
                sent = wait_call(lambda: client.send(greeting), 3000)
                if not sent:
                    raise OSError("failed to send TCP control message")
                greeting = greeting[sent:]
            return transfer(client, direction == "board_to_pc", rate, seconds)
        finally:
            client.close()
    finally:
        listener.close()


def board_main(wifi):
    import json
    import network

    start(415)
    ap = network.WLAN(network.AP_IF)
    try:
        ap.config(ssid=wifi["ssid"], key=wifi["password"], channel=wifi["channel"])
    except TypeError:
        ap.config(ssid=wifi["ssid"], key=wifi["password"])
    address = ap.ifconfig()[0]
    if address == "0.0.0.0":
        raise OSError("AP has no address")
    print("HCV_LINK_AP,%s" % address)
    for index, trial in enumerate(TRIALS):
        try:
            result = serve(address, index, trial)
        except OSError as error:
            result = {"error": error.args[0] if error.args else type(error).__name__}
        try:
            rssi = ap.status("rssi")
            result["rssi"] = rssi if isinstance(rssi, int) and -127 <= rssi < 0 else None
        except (AttributeError, OSError, TypeError, ValueError):
            result["rssi"] = None
        print("HCV_LINK_RESULT,%d,%s" % (index, json.dumps(result)))


if "HCV_LINK_WIFI" in globals():
    board_main(HCV_LINK_WIFI)
