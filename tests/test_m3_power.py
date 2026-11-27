from types import SimpleNamespace

import pytest

from station.bench import power


@pytest.mark.parametrize("disappears,attempts,finished_at", [(2, 1, 12), (10, 2, 20), (None, 3, 24)])
def test_usb_removal_wait_and_retry(monkeypatch, disappears, attempts, finished_at):
    now = 0
    inserted = False
    prompts = []

    def sleep(seconds):
        nonlocal now
        now += seconds

    def ask(prompt):
        nonlocal inserted
        prompts.append(prompt)
        if "现在插回" in prompt:
            inserted = True
        return ""

    def boards():
        return ["board"] if inserted or disappears is None or now < disappears else []

    monkeypatch.setattr(power, "boards", boards)
    monkeypatch.setattr(power, "time", SimpleNamespace(monotonic=lambda: now, sleep=sleep))
    if disappears is None:
        with pytest.raises(RuntimeError, match="未验证"):
            power.reconnect(ask)
    else:
        power.reconnect(ask)
    assert now == finished_at
    assert sum("电脑还能看到" in prompt for prompt in prompts) == attempts - 1
    assert inserted == (disappears is not None)
