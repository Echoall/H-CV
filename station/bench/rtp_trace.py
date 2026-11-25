"""Count interleaved RTP packets on the existing VLC TCP connection."""

from contextlib import contextmanager
import re
import select
import socket
from threading import Event, Thread
import time
from urllib.parse import urlsplit


def messages(buffer):
    while buffer:
        if buffer[0] == 36:  # RTSP interleaved frame: '$', channel, 16-bit length
            if len(buffer) < 4:
                return
            size = 4 + int.from_bytes(buffer[2:4], "big")
            if len(buffer) < size:
                return
            yield bytes(buffer[:size]), b""
        else:
            end = buffer.find(b"\r\n\r\n")
            if end < 0:
                if len(buffer) > 65536:
                    raise ValueError("invalid RTSP header")
                return
            header = bytes(buffer[:end + 4])
            length = re.search(rb"(?im)^Content-Length:\s*(\d+)\r?$", header)
            size = len(header) + (int(length[1]) if length else 0)
            if len(buffer) < size:
                return
            yield header, bytes(buffer[len(header):size])
        del buffer[:size]


@contextmanager
def trace(endpoint, stats):
    target = urlsplit(endpoint)
    cancelled = Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.25)
        local = f"rtsp://127.0.0.1:{listener.getsockname()[1]}"
        upstream = f"rtsp://{target.netloc}".encode("ascii")
        local_bytes = local.encode("ascii")
        buckets = {}
        stats.update(packets=0, bytes=0, by_second=buckets)

        def relay():
            last_ns = None
            peer = "player"
            stage = "accept"
            try:
                while not cancelled.is_set():
                    try:
                        client, _ = listener.accept()
                        break
                    except TimeoutError:
                        continue
                else:
                    return
                peer, stage = "board", "connect"
                with client, socket.create_connection((target.hostname, target.port), timeout=3) as board:
                    client.settimeout(3)
                    buffers = {client: bytearray(), board: bytearray()}
                    while not cancelled.is_set():
                        ready, _, _ = select.select([client, board], [], [], 0.25)
                        for source in ready:
                            peer, stage = ("board" if source is board else "player"), "recv"
                            data = source.recv(65536)
                            if not data:
                                stats["closed_by"] = peer
                                return
                            stamp = time.time_ns()
                            buffer = buffers[source]
                            buffer.extend(data)
                            for header, body in messages(buffer):
                                if header.startswith(b"$"):
                                    if (source is board and len(header) >= 16
                                            and header[1] == 0 and header[4] >> 6 == 2):
                                        size = len(header) - 4
                                        first = stamp if last_ns is None else last_ns
                                        stats.setdefault("first_at_ms", stamp // 1_000_000)
                                        stats["last_at_ms"] = stamp // 1_000_000
                                        stats["max_gap_ms"] = max(
                                            stats.get("max_gap_ms", 0), (stamp - first) // 1_000_000,
                                        )
                                        stats["packets"] += 1
                                        stats["bytes"] += size
                                        bucket = buckets.setdefault(str(stamp // 1_000_000_000), [0, 0])
                                        bucket[0] += 1
                                        bucket[1] += size
                                        last_ns = stamp
                                elif source is board:
                                    header = header.replace(upstream, local_bytes)
                                else:
                                    header = header.replace(local_bytes, upstream)
                                destination = client if source is board else board
                                peer, stage = ("player" if source is board else "board"), "send"
                                destination.sendall(header + body)
            except (OSError, ValueError) as error:
                if not cancelled.is_set():
                    stats["error"] = {"type": type(error).__name__, "peer": peer, "stage": stage}

        worker = Thread(target=relay, daemon=True)
        worker.start()
        try:
            yield local + target.path
        finally:
            cancelled.set()
            worker.join(7)
