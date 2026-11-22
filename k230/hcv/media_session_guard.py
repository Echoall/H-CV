"""Fail-closed guard for one media session per power cycle."""

import os
import time


_MARKER = "/tmp/hcv-media-session-used"


def _now_ticks():
    ticks_us = getattr(time, "ticks_us", None)
    if ticks_us is not None:
        return ticks_us()
    return int(time.monotonic() * 1_000_000)


def _ticks_diff(now, previous):
    ticks_diff = getattr(time, "ticks_diff", None)
    if ticks_diff is not None:
        return ticks_diff(now, previous)
    return now - previous


def _read_marker():
    with open(_MARKER, "r") as marker:
        value = marker.read().strip()
    if not value.isdigit():
        raise RuntimeError("HCV_MEDIA_SESSION_MARKER_INVALID")
    return int(value)


def _remove_marker():
    remove = getattr(os, "remove", None) or os.unlink
    remove(_MARKER)


def claim():
    """Claim the current boot for one media session."""
    now = _now_ticks()
    try:
        previous = _read_marker()
    except OSError:
        previous = None
    except RuntimeError:
        raise

    if previous is not None:
        if _ticks_diff(now, previous) >= 0:
            raise RuntimeError("HCV_MEDIA_SESSION_ALREADY_USED")
        try:
            _remove_marker()
        except OSError:
            raise RuntimeError("HCV_MEDIA_SESSION_GUARD_UNAVAILABLE")

    try:
        with open(_MARKER, "w") as marker:
            marker.write(str(now))
    except OSError:
        raise RuntimeError("HCV_MEDIA_SESSION_GUARD_UNAVAILABLE")
    print("HCV_MEDIA_GUARD,claimed")
