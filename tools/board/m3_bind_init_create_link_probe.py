"""Probe MediaManager.init, Create, and link in that order."""

import sys

sys.path.insert(0, "/sdcard/hcv")
from hcv.stream_bind_probe import run

run("init_create_link")
