"""Fail-closed guard for one media session per power cycle."""

import os
import time


_PRIMARY_MARKER = "/tmp/hcv-media-session-used"
_FALLBACK_MARKER = "/sdcard/hcv/run/hcv-media-session-used"


def _now_ticks():
    ticks_ms = getattr(time, "ticks_ms", None)
    if ticks_ms is not None:
        return ticks_ms()
    return int(time.monotonic() * 1_000)


def _ticks_diff(now, previous):
    ticks_diff = getattr(time, "ticks_diff", None)
    if ticks_diff is not None:
        return ticks_diff(now, previous)
    return now - previous


def _marker_path():
    try:
        os.stat("/tmp")
    except OSError:
        return _FALLBACK_MARKER
    return _PRIMARY_MARKER


def _read_marker(path):
    with open(path, "r") as marker:
        value = marker.read().strip()
    if not value.isdigit():
        raise RuntimeError("HCV_MEDIA_SESSION_MARKER_INVALID")
    return int(value)


def _remove_marker(path):
    remove = getattr(os, "remove", None) or os.unlink
    remove(path)


def claim():
    """Claim the current boot for one media session."""
    now = _now_ticks()
    marker_path = _marker_path()
    try:
        previous = _read_marker(marker_path)
    except OSError:
        previous = None
    except RuntimeError:
        raise

    if previous is not None:
        if _ticks_diff(now, previous) >= 0:
            raise RuntimeError("HCV_MEDIA_SESSION_ALREADY_USED")
        try:
            _remove_marker(marker_path)
        except OSError:
            raise RuntimeError("HCV_MEDIA_SESSION_GUARD_UNAVAILABLE")

    try:
        with open(marker_path, "w") as marker:
            marker.write(str(now))
    except OSError:
        raise RuntimeError("HCV_MEDIA_SESSION_GUARD_UNAVAILABLE")
    print("HCV_MEDIA_GUARD,claimed")
