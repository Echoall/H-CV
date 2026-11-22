"""Fail-closed guard for one media session per power cycle."""

import os


_MARKER = "/tmp/hcv-media-session-used"


def claim():
    """Claim the current boot for one media session."""
    try:
        os.stat(_MARKER)
    except OSError:
        pass
    else:
        raise RuntimeError("HCV_MEDIA_SESSION_ALREADY_USED")

    try:
        with open(_MARKER, "w") as marker:
            marker.write("1")
    except OSError:
        raise RuntimeError("HCV_MEDIA_SESSION_GUARD_UNAVAILABLE")
    print("HCV_MEDIA_GUARD,claimed")
