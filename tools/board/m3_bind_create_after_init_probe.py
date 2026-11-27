"""Probe MediaManager.init followed by encoder creation."""

import sys

sys.path.insert(0, "/sdcard/hcv")
sys.modules.pop("hcv.stream_bind_probe", None)
from hcv.stream_bind_probe import run

run("create_after_init")
