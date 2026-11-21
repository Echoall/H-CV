"""USB manual startup prototype.

Run this with the board tool. Do not copy it to ``/sdcard/main.py``.
The script checks escape before any camera import and arms only
``SimulatedWatchdog``. Hardware reset tests are not part of this file.
"""

import os
import sys
import time


def _boot_module():
    if os.path.isdir("/sdcard/hcv") and (not sys.path or sys.path[0] != "/sdcard/hcv"):
        sys.path.insert(0, "/sdcard/hcv")
    try:
        from hcv import boot as boot_mod
    except ImportError:
        from k230.hcv import boot as boot_mod
    return boot_mod


def run_manual(root, link, watchdog=None, interrupted=None, sleep=None,
               fault=None, camera_import=None, ctrl_c_window_s=1.5,
               heartbeats=1):
    boot_mod = _boot_module()
    if watchdog is None:
        watchdog = boot_mod.SimulatedWatchdog()
    if interrupted is None:
        interrupted = lambda: False
    if sleep is None:
        sleep = time.sleep
    ctrl_c = boot_mod.ctrl_c_seen(ctrl_c_window_s, sleep, interrupted)
    session = boot_mod.BootSession(
        boot_mod.DirectoryStore(root),
        link,
        watchdog,
        ctrl_c=ctrl_c,
        fault=fault,
        camera_import=camera_import,
    )
    return session.run(heartbeats=heartbeats)


class _WallClock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        time.sleep(seconds)
        self.now += float(seconds)


def main():
    boot_mod = _boot_module()
    result = run_manual(
        "/sdcard/hcv", boot_mod.StatusLink(available=False), heartbeats=0,
    )
    if result.mode != "measure":
        result.hold(_WallClock(), boot_mod.MANUAL_GUARD_S)
    print("boot_mode", result.mode)
    print("boot_wrote", int(result.wrote))
    print("allow_camera", int(result.allow_camera))
    print("wdt_enabled", int(result.watchdog.enabled))


if __name__ == "__main__":
    main()
