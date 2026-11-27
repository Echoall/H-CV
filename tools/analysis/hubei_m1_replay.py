"""Run the board M1 loop on recorded ROI pixels with a paced CHN1 and a video worker stub."""

import argparse
from contextlib import ExitStack, redirect_stdout
import gc
import json
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def clock_us():
    return time.perf_counter_ns() // 1000


class ReplaySensor:
    def __init__(self, fps, target):
        directory = ROOT / "reports/data/P1-T01/board-roi3/p1_t01_sequence_roi3"
        self.frames = [np.fromfile(p, np.uint8).reshape(64, 640)
                       for p in sorted(directory.glob("frame_*.gray.bin"))]
        if len(self.frames) != 30:
            raise ValueError("expected 30 archived camera strips")
        if target:
            # The original strip is near black. Give the synthetic positive control
            # a visible background before alternating marked and empty strips.
            self.frames = [np.clip(frame.astype(np.int16) + 128, 0, 255).astype(np.uint8)
                           for frame in self.frames]
            y, x = np.ogrid[:64, :640]
            mask = (x - 320) ** 2 + (y - 26) ** 2 <= 10 ** 2
            marked = [frame.copy() for frame in self.frames]
            for frame in marked:
                frame[mask] = 0
            self.frames = self.frames * 4 + marked * 4
        self.start = clock_us()
        self.slot = -1
        self.fps = fps

    def snapshot(self, chn, timeout):
        assert chn == 1 and timeout == 1000
        due = self.start + (self.slot + 1) * 1_000_000 / self.fps
        time.sleep(max(0, due - clock_us()) / 1_000_000)
        self.slot = max(self.slot + 1, (clock_us() - self.start) * self.fps // 1_000_000)
        return self

    def crop(self, roi, alloc, data):
        x, y, w, h = roi
        strip = self.frames[self.slot % len(self.frames)]
        self.roi = strip[y - 148:y - 148 + h, x:x + w]
        data[:] = self.roi.tobytes()
        return self

    def mean_pooled(self, x, y, alloc, data):
        data[:] = self.roi.mean(axis=0).astype(np.uint8).tobytes()


def replay(output, seconds, fps, target):
    sys.path.insert(0, str(ROOT / "k230"))
    from hcv.hubei_detection import HubeiDetection
    from hcv.proto import crc8

    config = json.loads((ROOT / "reports/data/M1/m1-config.json").read_text())
    if fps == 60:
        config["sensor"].update(width=1920, height=1080, fps=60)
    settings = json.loads((ROOT / "station/bench/hubei_detection.json").read_text())
    dual = HubeiDetection(config, settings)
    dual.server = SimpleNamespace(thread_over=False)
    packets = {1: 0, 2: 0}

    def write(frame):
        assert frame[:2] == b"\xa5\x5a" and frame[2] + 4 == len(frame)
        assert frame[-1] == crc8(frame[2:-1])
        packets[frame[3]] += 1

    stop = threading.Event()
    beats = []

    def video():
        while not stop.wait(1 / 60):
            beats.append(clock_us())

    modules = {}
    for name, attrs in {
        "ulab": {"numpy": np}, "image": {"ALLOC_REF": 0}, "media": {},
        "media.media": {"MediaManager": object}, "media.sensor": {"Sensor": object},
        "machine": {"FPIOA": object, "UART": object},
    }.items():
        modules[name] = ModuleType(name)
        modules[name].__dict__.update(attrs)
    modules["ulab.numpy"] = np
    with ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, modules))
        import main_hcv as m1
        for name, func in {"ticks_us": clock_us, "ticks_ms": lambda: clock_us() // 1000,
                           "ticks_diff": lambda a, b: a - b, "ticks_add": lambda a, b: a + b,
                           "sleep_ms": lambda ms: time.sleep(ms / 1000)}.items():
            stack.enter_context(patch.object(time, name, func, create=True))
        stack.enter_context(patch.object(gc, "mem_free", lambda: -1, create=True))
        stack.enter_context(patch.object(m1, "UART1Link", lambda baud: SimpleNamespace(
            uart=SimpleNamespace(write=write), close=lambda: None)))
        dual.sensor = ReplaySensor(fps, target)
        thread = threading.Thread(target=video)
        thread.start()
        try:
            with output.open("x", encoding="utf-8") as log, redirect_stdout(log):
                print("HCV_REPLAY,fps=%d,seconds=%d,yield_ms=%d,synthetic_target=%d" %
                      (fps, seconds, dual.delay, target))
                m1.run(max_runtime_s=seconds, shared=dual)
                print("HCV_REPLAY_END,ball=%d,status=%d,video_ticks=%d" % (packets[1], packets[2], len(beats)))
        finally:
            stop.set()
            thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=int, default=65)
    parser.add_argument("--fps", type=int, choices=(60, 90), default=90)
    parser.add_argument("--target", action="store_true", help="overlay a synthetic disk on alternate sequences")
    args = parser.parse_args()
    replay(args.output, args.seconds, args.fps, args.target)
