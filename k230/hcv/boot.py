"""One-write boot record shared by the manual prototype and later production.

Each power cycle commits at most one ``boot.state``. The commit is a temp
file plus rename. A torn temp is ignored when the previous commit is intact.
An unreadable commit counts as one ``STARTING``. Two consecutive unfinished
``STARTING`` records enter maintenance. Maintenance and escape never enable
the watchdog and never import a camera.
"""

import os

from .proto import crc8, pack_status_into


STATE_FILE = "boot.state"
TEMP_FILE = "boot.state.tmp"
SKIP_FILE = "SKIP_AUTOSTART"
WDT_TIMEOUT_S = 1.0
MAINTENANCE_STREAK = 2
RUNTIME_FAIL_LIMIT = 3
STATUS_PERIOD_S = 0.5
MANUAL_GUARD_S = 5.0
STATE_BOOT = 0
STATE_FAULT = 4
ERR_UNKNOWN = 255
ERR_STREAM = 9
_CTRL_C_STEP_S = 0.05


class BootRecord:
    def __init__(self, phase, streak, generation, corrupt):
        self.phase = phase
        self.streak = streak
        self.generation = generation
        self.corrupt = corrupt


class ManualClock:
    """Test clock. Production code can pass a clock whose sleep calls time.sleep."""

    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        if seconds < 0:
            raise ValueError("sleep seconds must be >= 0")
        self.now += float(seconds)


class SimulatedWatchdog:
    """Stand-in for the hardware watchdog. The manual prototype arms only this."""

    def __init__(self):
        self.enabled = False
        self.timeout_s = None
        self.feeds = 0
        self.resets = 0
        self.expired = False
        self.armed_at = None
        self.last_feed_at = None

    def enable(self, timeout_s, now=0.0):
        if timeout_s < WDT_TIMEOUT_S:
            raise ValueError("watchdog timeout must be at least 1s")
        self.enabled = True
        self.timeout_s = timeout_s
        self.expired = False
        self.armed_at = float(now)
        self.last_feed_at = float(now)

    def disable(self):
        self.enabled = False

    def observe(self, now):
        if not self.enabled:
            return False
        if self.expired:
            return True
        if float(now) - self.last_feed_at >= self.timeout_s:
            self.expired = True
        return self.expired

    def feed(self):
        if not self.enabled:
            raise RuntimeError("watchdog feed before enable")
        if self.expired:
            raise RuntimeError("watchdog feed after expiry")
        self.feeds += 1

    def note_feed_time(self, now):
        if self.observe(now):
            raise RuntimeError("watchdog feed after expiry")
        self.last_feed_at = float(now)


class StatusLink:
    def __init__(self, available=True):
        self.available = available
        self.sent = []

    def send(self, state, err, stream=0):
        if not self.available:
            return None
        out = bytearray(18)
        pack_status_into(
            out, len(self.sent) & 0xFF, state, err, 0, 0, 0, stream, 0, 0x0100,
        )
        frame = bytes(out)
        self.sent.append(frame)
        return frame


class MemoryStore:
    def __init__(self):
        self.files = {}
        self.commits = 0
        self.fail_after_temp = False

    def read(self, name):
        return self.files.get(name)

    def exists(self, name):
        return name in self.files

    def commit(self, name, temp, data):
        self.commits += 1
        self.files[temp] = bytes(data)
        if self.fail_after_temp:
            raise OSError("power lost before rename")
        self.files[name] = bytes(data)
        del self.files[temp]


class DirectoryStore:
    def __init__(self, root):
        self.root = root
        self.commits = 0

    def _path(self, name):
        return os.path.join(self.root, name)

    def read(self, name):
        try:
            handle = open(self._path(name), "rb")
        except OSError:
            return None
        try:
            return handle.read()
        finally:
            handle.close()

    def exists(self, name):
        return self.read(name) is not None

    def commit(self, name, temp, data):
        self.commits += 1
        temp_path = self._path(temp)
        final_path = self._path(name)
        handle = open(temp_path, "wb")
        try:
            handle.write(data)
            handle.flush()
        finally:
            handle.close()
        if hasattr(os, "replace"):
            os.replace(temp_path, final_path)
            return
        try:
            os.remove(final_path)
        except OSError:
            pass
        os.rename(temp_path, final_path)


def format_record(phase, streak, generation):
    body = "HCVBOOT1 %s %d %d" % (phase, int(streak), int(generation))
    return (body + " %02X\n" % crc8(body.encode("ascii"))).encode("ascii")


def parse_record(raw):
    if raw is None:
        return BootRecord("ABSENT", 0, 0, False)
    try:
        text = raw.decode("ascii").strip()
        parts = text.split(" ")
        if len(parts) != 5 or parts[0] != "HCVBOOT1":
            raise ValueError
        phase = parts[1]
        streak = int(parts[2])
        generation = int(parts[3])
        digest = int(parts[4], 16)
        body = "HCVBOOT1 %s %d %d" % (phase, streak, generation)
        if digest != crc8(body.encode("ascii")) or generation < 1:
            raise ValueError
        if phase == "STARTING" and streak < 1:
            raise ValueError
        if phase == "RUNNING" and streak != 0:
            raise ValueError
        if phase not in ("STARTING", "RUNNING"):
            raise ValueError
    except (ValueError, UnicodeError):
        return BootRecord("STARTING", 1, 0, True)
    return BootRecord(phase, streak, generation, False)


def prefer(committed, temporary):
    if committed is None and temporary is None:
        return BootRecord("ABSENT", 0, 0, False)
    if committed is not None and temporary is not None:
        if committed.corrupt and temporary.corrupt:
            return BootRecord("STARTING", 1, 0, True)
        if committed.corrupt:
            return temporary
        if temporary.corrupt:
            return committed
        if temporary.generation > committed.generation:
            return temporary
        return committed
    return committed if committed is not None else temporary


def load_record(store):
    committed = _read_one(store, STATE_FILE)
    temporary = _read_one(store, TEMP_FILE)
    return prefer(committed, temporary)


def _read_one(store, name):
    raw = store.read(name)
    if raw is None:
        return None
    return parse_record(raw)


def next_generation(record):
    if record.generation < 1:
        return 1
    return record.generation + 1


def ctrl_c_seen(duration_s, sleep, interrupted):
    """Poll an already-separated Ctrl-C check. True means escape."""
    if duration_s < 0:
        raise ValueError("duration_s must be >= 0")
    remaining = float(duration_s)
    while remaining > 0:
        if interrupted():
            return True
        step = _CTRL_C_STEP_S if remaining > _CTRL_C_STEP_S else remaining
        sleep(step)
        remaining -= step
    return bool(interrupted())


class BootSession:
    def __init__(self, store, link, watchdog, ctrl_c=False, fault=None,
                 camera_import=None):
        self.store = store
        self.link = link
        self.watchdog = watchdog
        self.ctrl_c = ctrl_c
        self.fault = fault
        self.camera_import = camera_import
        self.mode = None
        self.record = None
        self.wrote = False
        self.allow_camera = False
        self._commits = 0
        self._done = False

    def run(self, heartbeats=1):
        if self._done:
            raise RuntimeError("boot session already ran")
        if heartbeats < 0:
            raise ValueError("heartbeats must be >= 0")
        self._done = True
        if self.ctrl_c or self.store.exists(SKIP_FILE):
            return self._finish("escape", load_record(self.store), False, heartbeats)
        record = load_record(self.store)
        if record.phase == "STARTING" and record.streak >= MAINTENANCE_STREAK:
            return self._finish("maintenance", record, False, heartbeats)
        if self.fault == "before_loop":
            streak = record.streak + 1 if record.phase == "STARTING" else 1
            written = self._write("STARTING", streak, next_generation(record))
            mode = "maintenance" if streak >= MAINTENANCE_STREAK else "failed"
            return self._finish(mode, written, True, heartbeats)
        if self.fault is not None:
            raise ValueError("unknown fault")
        written = self._write("RUNNING", 0, next_generation(record))
        self.watchdog.enable(WDT_TIMEOUT_S)
        self.allow_camera = True
        result = self._finish("measure", written, True, 0)
        if self.camera_import is not None:
            self.camera_import()
        return result

    def on_frame_complete(self):
        if self.mode != "measure":
            raise RuntimeError("frame complete outside measurement")
        self.watchdog.feed()

    def hold(self, clock, deadline_s, period_s=STATUS_PERIOD_S):
        """Send FAULT until the guard deadline. Does not arm the watchdog."""
        if self.mode == "measure":
            raise RuntimeError("measurement does not use the fault hold")
        if self.watchdog.enabled:
            raise RuntimeError("watchdog enabled outside measurement")
        if deadline_s < 0 or period_s <= 0:
            raise ValueError("hold timing must be positive")
        commits = self._commits
        while clock.now < deadline_s:
            self.link.send(STATE_FAULT, ERR_UNKNOWN, 0)
            remaining = deadline_s - clock.now
            clock.sleep(period_s if remaining > period_s else remaining)
        if self._commits != commits or self.watchdog.enabled:
            raise RuntimeError("fault hold changed boot state or watchdog")

    def run_frames(self, clock, frame, count, deadline_s):
        """Run a bounded measurement. Feed only after a frame returns in time."""
        if self.mode != "measure" or self._commits != 1:
            raise RuntimeError("measurement requires the single RUNNING commit")
        if count < 0 or deadline_s < 0:
            raise ValueError("frame budget must be >= 0")
        commits = self._commits
        completed = 0
        consecutive_failures = 0
        while completed < count and clock.now < deadline_s and self.mode == "measure":
            try:
                outcome = frame()
            except Exception:
                if self.watchdog.observe(clock.now):
                    self._stop_measurement("watchdog_expired")
                    break
                consecutive_failures += 1
                if consecutive_failures >= RUNTIME_FAIL_LIMIT:
                    self._stop_measurement("faulted")
                continue
            if self.watchdog.observe(clock.now):
                self._stop_measurement("watchdog_expired")
                break
            consecutive_failures = 0
            completed += 1
            if outcome == "stream_error":
                self.note_stream_error()
            self.on_frame_complete()
            self.watchdog.note_feed_time(clock.now)
        if self._commits != commits:
            raise RuntimeError("frame loop wrote boot state")
        return completed

    def _stop_measurement(self, mode):
        self.mode = mode
        self.allow_camera = False
        self.watchdog.disable()

    def note_stream_error(self):
        if self.mode != "measure":
            raise RuntimeError("stream error outside measurement")
        self.link.send(STATE_BOOT, ERR_STREAM, 3)

    def _write(self, phase, streak, generation):
        if self._commits:
            raise RuntimeError("second boot-state write")
        self._commits = 1
        data = format_record(phase, streak, generation)
        self.store.commit(STATE_FILE, TEMP_FILE, data)
        return BootRecord(phase, streak, generation, False)

    def _finish(self, mode, record, wrote, heartbeats):
        self.mode = mode
        self.record = record
        self.wrote = wrote
        if mode != "measure":
            self.allow_camera = False
            for _ in range(heartbeats):
                self.link.send(STATE_FAULT, ERR_UNKNOWN, 0)
        return self
