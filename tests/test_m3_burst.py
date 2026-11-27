import errno
import struct

import pytest

from tools.board import m3_link_check as probe


@pytest.mark.parametrize("shape,count,first", [("A", 190, 1000), ("B", 30, 6333),
                                                ("C", 10, 19000), ("D", 31, 40000)])
def test_one_second_schedules(shape, count, first):
    slots = list(probe.burst_schedule(shape))
    assert len(slots) == count and slots[0] == (0, first)
    assert sum(size for _, size in slots) == 190000
    assert all(0 <= a < b < 1000 for (a, _), (b, _) in zip(slots, slots[1:]))
    if shape == "D":
        assert all(size == 5000 for _, size in slots[1:])


def test_udp_d_burst_preserves_numbering_and_measures_wait(monkeypatch):
    clock = [0]
    monkeypatch.setattr(probe, "ticks_ms", lambda: clock[0])
    monkeypatch.setattr(probe, "sleep_ms", lambda ms: clock.__setitem__(0, clock[0] + ms))
    sequences = []

    class Socket:
        def setblocking(self, enabled):
            assert not enabled

        def sendto(self, data, peer):
            assert len(data) == 1000
            sequences.append(struct.unpack_from(">I", data)[0])
            clock[0] += 1
            return len(data)

    result = probe.send_bursts(Socket(), "D", 1, ("127.0.0.1", 1234))
    assert sequences == list(range(190))
    assert result["completed_bursts"] == 31 and result["bytes"] == 190000
    assert result["send_burst_max_ms"] == result["send_burst_p99_ms"] == 40


def test_partial_tcp_send_then_reset_preserves_failed_burst_time(monkeypatch):
    clock = [0]
    monkeypatch.setattr(probe, "ticks_ms", lambda: clock[0])

    class Socket:
        def setblocking(self, enabled):
            pass

        def send(self, data):
            clock[0] += 10
            if clock[0] > 10:
                raise OSError(errno.ECONNRESET)
            assert len(data) == 19000
            return 4000

    result = probe.send_bursts(Socket(), "C", 1)
    assert result["error"] == errno.ECONNRESET and result["bytes"] == 4000
    assert result["error_at_ms"] == result["send_burst_max_ms"] == 20
    assert result["completed_bursts"] == 0 and result["send_burst_p99_ms"] is None
