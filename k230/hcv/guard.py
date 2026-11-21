"""CanMV board-side deadline and interrupt checkpoints."""

import os
import time


_deadline = None


def start(deadline_s):
    """Enable CanMV exit points and set a deadline in seconds."""
    global _deadline
    if deadline_s <= 0:
        raise ValueError("deadline_s must be positive")
    _deadline = time.ticks_add(time.ticks_ms(), int(1000 * deadline_s))
    if hasattr(os, "EXITPOINT_ENABLE"):
        os.exitpoint(os.EXITPOINT_ENABLE)


def tick():
    """Run inside loops so the script can stop independently of the host."""
    if _deadline is None:
        raise RuntimeError("guard.start must be called before guard.tick")
    if hasattr(os, "exitpoint"):
        os.exitpoint()
    if time.ticks_diff(_deadline, time.ticks_ms()) <= 0:
        raise RuntimeError("board-side guard deadline exceeded")
