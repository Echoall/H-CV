"""Fixed-storage timing windows used by the M1 board runner."""

from array import array


STAGES = ("snapshot", "detect", "map", "track", "pack", "uart", "process", "total", "gc")


class TimingWindow:
    def __init__(self, capacity=128):
        self.capacity = int(capacity)
        self.values = [array("I", [0] * self.capacity) for _ in STAGES]
        self.counts = [0] * len(STAGES)
        self.frame_count = 0
        self.first_us = None
        self.last_us = None

    def add(self, stage, duration_us):
        index = stage if isinstance(stage, int) else STAGES.index(stage)
        count = self.counts[index]
        self.values[index][count % self.capacity] = max(0, int(duration_us))
        if count < self.capacity:
            self.counts[index] = count + 1

    def frame(self, t_cap_us):
        value = int(t_cap_us)
        if self.first_us is None:
            self.first_us = value
        self.last_us = value
        self.frame_count += 1

    def reset(self):
        self.counts = [0] * len(STAGES)
        self.frame_count = 0
        self.first_us = None
        self.last_us = None

    def _percentile(self, index, fraction):
        count = self.counts[index]
        if count == 0:
            return 0
        ordered = [self.values[index][offset] for offset in range(count)]
        ordered.sort()
        position = int((count - 1) * fraction)
        return ordered[position]

    def stats(self, index):
        count = self.counts[index]
        if count == 0:
            return (0, 0, 0, 0, 0)
        return (
            count,
            self._percentile(index, 0.50),
            self._percentile(index, 0.95),
            self._percentile(index, 0.99),
            max(self.values[index][offset] for offset in range(count)),
        )

    def all_stats(self):
        return [self.stats(index) for index in range(len(STAGES))]
