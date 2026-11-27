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
UDP_TRIALS = tuple(trial for trial in TRIALS if trial[0] == "udp")
BURST_SHAPES = ("A", "B", "C", "D", "D")
BURST_TRIALS = tuple(("udp" if i == 4 else "tcp", "board_to_pc", 190, 60) for i in range(5))
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


def wait_call(action, limit_ms, accept=None):
    started = ticks_ms()
    while ticks_diff(ticks_ms(), started) < limit_ms:
        tick()
        try:
            result = action()
        except OSError as error:
            if not no_data(error):
                raise
        else:
            if accept is None or accept(result):
                return result
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
                if seen is not None:
                    largest_gap = max(largest_gap, seconds * 1000 - ticks_diff(last_activity, started))
                    break
                raise OSError("trial deadline")
            if seen is None and ticks_diff(now, last_activity) >= 4000:
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
        "error_at_ms": ticks_diff(ticks_ms(), started) if error is not None else None,
        "active_ms": ticks_diff(last_activity, started), "max_activity_gap_ms": largest_gap,
        "expected_bytes": blocks * BLOCK_BYTES, "error": error,
        "missing_datagrams": blocks - len(seen) if seen is not None else None,
    }


def banner(index):
    return ("HCVLINK%d\n" % index).encode()


def burst_schedule(shape):
    """One second of (due ms, bytes), exactly 190,000 bytes for every shape."""
    if shape == "D":
        yield 0, 40_000
        for i in range(1, 31):
            yield i * 1000 // 31, 5000
    else:
        count = {"A": 190, "B": 30, "C": 10}[shape]
        for i in range(count):
            yield i * 1000 // count, (i + 1) * 190_000 // count - i * 190_000 // count


def send_bursts(sock, shape, seconds, peer=None, trial_index=None):
    schedule = tuple(burst_schedule(shape))
    block = memoryview(b"K" * max(size for _, size in schedule))
    datagram = bytearray(DATA) if peer else None
    histogram = {}
    sent = completed = longest = 0
    failure = None
    sock.setblocking(False)
    started = ticks_ms()
    burst_start = None
    try:
        for second in range(seconds):
            for due, size in schedule:
                tick()
                delay = second * 1000 + due - ticks_diff(ticks_ms(), started)
                if delay > 0:
                    sleep_ms(delay)
                burst_start = ticks_ms()
                offset = 0
                while offset < size:
                    tick()
                    if ticks_diff(ticks_ms(), burst_start) >= 4000:
                        raise OSError("burst send stalled")
                    try:
                        if peer is None:
                            n = sock.send(block[offset:size])
                        else:
                            struct.pack_into(">I", datagram, 0, sent // BLOCK_BYTES)
                            n = sock.sendto(datagram, peer)
                            if n != BLOCK_BYTES:
                                raise OSError("short datagram")
                    except OSError as error:
                        if not no_data(error):
                            raise
                        sleep_ms(1)
                        continue
                    if not n:
                        raise OSError("socket sent zero bytes")
                    offset += n
                    sent += n
                duration = ticks_diff(ticks_ms(), burst_start)
                longest = max(longest, duration)
                histogram[duration] = histogram.get(duration, 0) + 1
                completed += 1
                burst_start = None
            if trial_index is not None:
                print("HCV_LINK_PROGRESS,%d,%d,%d,%d,%d" % (
                    trial_index, ticks_diff(ticks_ms(), started), sent, completed, longest))
        delay = seconds * 1000 - ticks_diff(ticks_ms(), started)
        if delay > 0:
            sleep_ms(delay)
    except OSError as error:
        failure = error.args[0] if error.args else type(error).__name__
        if burst_start is not None:
            longest = max(longest, ticks_diff(ticks_ms(), burst_start))
    elapsed = ticks_diff(ticks_ms(), started)
    rank = (99 * completed + 99) // 100
    p99 = None
    for duration in sorted(histogram):
        rank -= histogram[duration]
        if rank <= 0:
            p99 = duration
            break
    return {"bytes": sent, "active_ms": elapsed, "elapsed_ms": elapsed, "error": failure,
            "error_at_ms": elapsed if failure is not None else None, "completed_bursts": completed,
            "send_burst_p99_ms": p99, "send_burst_max_ms": longest}


def serve(address, index, trial, *, udp_start="tcp", shape=None):
    protocol, direction, rate, seconds = trial
    datagram_start = protocol == "udp" and udp_start == "udp"
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if datagram_start else socket.SOCK_STREAM)
    datagrams = None
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.setblocking(False)
        listener.bind((address, PORT))
        if protocol == "udp" and not datagram_start:
            datagrams = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            datagrams.bind((address, PORT))
        if not datagram_start:
            listener.listen(1)
        print("HCV_LINK_READY,%d" % index)
        limit = 45_000 if index == 0 else 18_000
        if datagram_start:
            expected = banner(index)
            # CanMV returns an empty datagram with port 0 when a nonblocking socket is idle.
            _, peer = wait_call(lambda: listener.recvfrom(64), limit, lambda item: item[0] == expected)
            return transfer(listener, True, rate, seconds, peer)
        client, remote = wait_call(listener.accept, limit)
        try:
            client.setblocking(False)
            greeting = banner(index)
            while greeting:
                sent = wait_call(lambda: client.send(greeting), 3000)
                if not sent:
                    raise OSError("failed to send TCP control message")
                greeting = greeting[sent:]
            if protocol == "udp":
                port_bytes = b""
                while len(port_bytes) < 2:
                    part = wait_call(lambda: client.recv(2 - len(port_bytes)), 3000, bool)
                    port_bytes += part
                port = struct.unpack(">H", port_bytes)[0]
                if not port:
                    raise OSError("invalid UDP destination port")
                print("HCV_LINK_UDP_START,%d" % index)
                if shape:
                    return send_bursts(datagrams, shape, seconds, (remote[0], port), index)
                return transfer(datagrams, True, rate, seconds, (remote[0], port))
            if shape:
                return send_bursts(client, shape, seconds, trial_index=index)
            return transfer(client, direction == "board_to_pc", rate, seconds)
        finally:
            client.close()
    finally:
        if datagrams is not None:
            datagrams.close()
        listener.close()


def board_main(wifi, udp_only=False, burst=False):
    import json
    import network

    start(490 if burst else (155 if udp_only else 415))
    ap = network.WLAN(network.AP_IF)
    try:
        ap.config(ssid=wifi["ssid"], key=wifi["password"], channel=wifi["channel"])
    except TypeError:
        ap.config(ssid=wifi["ssid"], key=wifi["password"])
    address = ap.ifconfig()[0]
    if address == "0.0.0.0":
        raise OSError("AP has no address")
    print("HCV_LINK_AP,%s" % address)
    trials = BURST_TRIALS if burst else (UDP_TRIALS if udp_only else TRIALS)
    for index, trial in enumerate(trials):
        try:
            result = serve(address, index, trial, shape=BURST_SHAPES[index] if burst else None)
        except OSError as error:
            result = {"error": error.args[0] if error.args else type(error).__name__}
        try:
            rssi = ap.status("rssi")
            result["rssi"] = rssi if isinstance(rssi, int) and -127 <= rssi < 0 else None
        except (AttributeError, OSError, TypeError, ValueError):
            result["rssi"] = None
        print("HCV_LINK_RESULT,%d,%s" % (index, json.dumps(result)))


if "HCV_LINK_WIFI" in globals():
    board_main(HCV_LINK_WIFI, globals().get("HCV_LINK_UDP_ONLY", False), globals().get("HCV_LINK_BURST", False))
