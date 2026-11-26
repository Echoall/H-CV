"""CHN1 client of the Hubei camera session. The server owns all media resources."""

from array import array
import time


class HubeiDetection:
    def __init__(self, config, settings):
        self.config = config
        self.settings = settings
        self.delay = settings["yield_ms"]
        if settings["yield_method"] != "sleep_ms" or type(self.delay) is not int or not 0 <= self.delay <= 10:
            raise ValueError("expected sleep_ms and 0..10 ms")
        # Bound is one extra second beyond the fixed 125-second media session.
        capacity = 126 * config["sensor"]["fps"]
        self.process = array("I", [0] * capacity)
        self.after_capture = array("I", [0] * capacity)
        self.boundaries = array("I", [0] * (capacity // 60))

    def configure(self, sensor):
        from media.sensor import Sensor
        self.sensor = sensor
        mode = self.config["sensor"]
        channel = self.config["chn_detect"]
        if mode["hmirror"] or mode["vflip"] or channel["format"] != "GRAYSCALE":
            raise ValueError("combined run requires unmirrored grayscale detection")
        size = dict(width=channel["width"], height=channel["height"], chn=1)
        if channel["crop"] is not None:
            size["crop"] = channel["crop"]
        sensor.set_framesize(**size)
        sensor.set_pixformat(Sensor.GRAYSCALE, chn=1)

    def measure(self, server):
        import hubei_m1_runner
        self.server = server
        hubei_m1_runner.run(max_runtime_s=125, shared=self)
        if server.thread_over:
            raise RuntimeError("video worker exited before M1 finished")

    def begin(self, started_us):
        self.started_us = started_us
        print("HCV_DUAL_ORIGIN,%d" % time.ticks_diff(time.ticks_us(), started_us))

    def end_frame(self, count, process_us, captured_us):
        if self.server.thread_over:
            raise RuntimeError("video worker exited during M1")
        time.sleep_ms(self.delay)
        now = time.ticks_us()
        index = count - 1
        self.process[index] = process_us
        self.after_capture[index] = time.ticks_diff(now, captured_us)
        if count % 60 == 0:
            self.boundaries[count // 60 - 1] = time.ticks_diff(now, self.started_us)

    def report(self, count):
        previous = 0
        for window in range(count // 60):
            start = window * 60
            end = start + 60
            elapsed = self.boundaries[window]
            process = sorted(self.process[start:end])
            busy = sorted(self.after_capture[start:end])
            print("HCV_DUAL,%d,%d,%d,%d,%d,%d,%d,%d,%d" % (
                end, elapsed, elapsed - previous,
                process[29], process[56], process[58], busy[29], busy[56], busy[58]))
            previous = elapsed
