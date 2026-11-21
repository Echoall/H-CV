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


class SimulatedWatchdog:
    """Stand-in for the hardware watchdog. The manual prototype arms only this."""

    def __init__(self):
        self.enabled = False
        self.timeout_s = None
        self.feeds = 0

    def enable(self, timeout_s):
        if timeout_s < WDT_TIMEOUT_S:
            raise ValueError("watchdog timeout must be at least 1s")
        self.enabled = True
        self.timeout_s = timeout_s

    def feed(self):
        if not self.enabled:
            raise RuntimeError("watchdog feed before enable")
        self.feeds += 1


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
        if heartbeats < 1:
            raise ValueError("heartbeats must be >= 1")
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
