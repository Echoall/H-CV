"""Probe MediaManager.init, Create, and link in that order."""

import sys

sys.path.insert(0, "/sdcard/hcv")
sys.modules.pop("hcv.stream_bind_probe", None)
from hcv.stream_bind_probe import run

run("init_create_link")
