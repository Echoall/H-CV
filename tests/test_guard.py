from types import SimpleNamespace

import pytest

from k230.hcv import guard


def test_guard_enables_exitpoint_and_expires(monkeypatch):
    values = iter((10, 150, 1010))
    calls = []
    monkeypatch.setattr(guard, "os", SimpleNamespace(
        EXITPOINT_ENABLE=1, exitpoint=lambda *args: calls.append(args),
    ))
    monkeypatch.setattr(guard, "time", SimpleNamespace(
        ticks_ms=lambda: next(values),
        ticks_add=lambda a, b: a + b,
        ticks_diff=lambda a, b: a - b,
    ))
    guard.start(1)
    guard.tick()
    with pytest.raises(RuntimeError, match="deadline exceeded"):
        guard.tick()
    assert calls == [(1,), (), ()]
