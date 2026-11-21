"""Probe MediaManager.init followed by encoder creation and link."""

import sys

sys.path.insert(0, "/sdcard/hcv")
from hcv.stream_bind_probe import run

run("link_after_init")
