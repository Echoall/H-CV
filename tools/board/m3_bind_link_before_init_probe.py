"""Probe encoder creation and link before MediaManager.init."""

import sys

sys.path.insert(0, "/sdcard/hcv")
from hcv.stream_bind_probe import run

run("link_before_init")
