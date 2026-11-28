import builtins
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace

import pytest

from station.bench import hubei, sensor_reuse


class Camera:
    GRAYSCALE = 1

    def __init__(self, *, width, height, fps):
        assert (width, height, fps) == (1280, 720, 90)
        self.running = False

    def reset(self):
        assert not self.running

    def set_framesize(self, *, width, height, chn):
        assert (width, height, chn) == (320, 240, 0)

    def set_pixformat(self, pixel_format, *, chn):
        assert (pixel_format, chn) == (self.GRAYSCALE, 0)

    def run(self):
        self.running = True

    def snapshot(self, *, chn, timeout):
        assert self.running and (chn, timeout) == (0, 1000)
        return SimpleNamespace(width=lambda: 320, height=lambda: 240)

    def stop(self):
        assert self.running
        self.running = False


class SimulatedRepl:
    def __init__(self, failure=None):
        self.failure = failure
        self.namespace = {}
        self.calls = []
        self.summary = {}

    def command(self, name, args, **kwargs):
        self.calls.append(name)
        if name == "soft-reset":
            assert args[-1] == "soft-reset-confirmed"
            if self.failure != "same_namespace_after_reset":
                self.namespace.clear()
            return 0, "" if self.failure == "missing_banner" else "HCV_S0_SOFT_REBOOT_CONFIRMED", ""
        if name == "after-info":
            assert args[-1] == "info"
            return 0, "fixture-v18", ""
        if name == "same_runtime" and self.failure == "unexpected_reset":
            self.namespace.clear()
        modules = {
            "media.sensor": SimpleNamespace(Sensor=Camera),
            "time": SimpleNamespace(ticks_ms=lambda: 1, ticks_add=lambda a, b: a+b, ticks_diff=lambda a, b: a-b),
            "os": SimpleNamespace(uname=lambda: SimpleNamespace(machine="k230_canmv_hiwonder", release="v1.8-0")),
        }
        def load(module, *args):
            return modules[module] if module in modules else builtins.__import__(module, *args)
        self.namespace["__builtins__"] = dict(vars(builtins), __import__=load)
        output = StringIO()
        try:
            with redirect_stdout(output):
                exec(kwargs["data"], self.namespace)
        except RuntimeError as exc:
            return 1, output.getvalue(), str(exc)
        text = output.getvalue()
        if self.failure == "missing_release":
            text = text.split("HCV_S0_RELEASED")[0]
        return 0, text, ""


@pytest.mark.parametrize("failure,expected_calls", [
    (None, ["first", "same_runtime", "soft-reset", "after_reset", "after-info"]),
    ("unexpected_reset", ["first", "same_runtime"]),
    ("missing_release", ["first"]),
    ("missing_banner", ["first", "same_runtime", "soft-reset"]),
    ("same_namespace_after_reset", ["first", "same_runtime", "soft-reset", "after_reset"]),
])
def test_s0_executes_board_script_and_checks_repl_continuity(monkeypatch, failure, expected_calls):
    run = SimulatedRepl(failure)
    confirmations = []
    monkeypatch.setattr(hubei, "image_matches", lambda output, version: (output, version) == ("fixture-v18", "v1.8"))
    if failure:
        with pytest.raises(RuntimeError):
            sensor_reuse.measure(run, None, confirm=lambda _: confirmations.append(len(run.calls)), sensor_soft_reset=True)
    else:
        sensor_reuse.measure(run, None, confirm=lambda _: confirmations.append(len(run.calls)), sensor_soft_reset=True)
        assert run.summary["sensor_sessions"] == dict.fromkeys(sensor_reuse.STAGES, True)
    assert run.calls == expected_calls
    assert confirmations == [i for i, name in enumerate(run.calls) if name in sensor_reuse.STAGES]


def test_s0_cancel_before_capture_sends_no_script():
    run = SimulatedRepl()
    with pytest.raises(RuntimeError, match="取消"):
        sensor_reuse.measure(run, lambda _: "q")
    assert not run.calls


def test_s0a_two_captures_without_soft_reset(monkeypatch):
    run = SimulatedRepl()
    monkeypatch.setattr(hubei, "image_matches", lambda out, version: out == "fixture-v18" and version == "v1.8")
    confirmations = []
    sensor_reuse.measure(run, None, confirm=lambda _: confirmations.append(len(run.calls)))
    assert run.calls == ["first", "same_runtime", "after-info"]
    assert confirmations == [0, 1]
    assert not run.summary["soft_reboot_confirmed"]
