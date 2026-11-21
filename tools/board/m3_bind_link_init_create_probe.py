"""Probe link, MediaManager.init, and Create in official-example order."""

import sys

sys.path.insert(0, "/sdcard/hcv")
sys.modules.pop("hcv.stream_bind_probe", None)
from hcv.stream_bind_probe import run

run("link_init_create")
