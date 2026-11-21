"""Probe Create, link, and MediaManager.init in that order."""

import sys

sys.path.insert(0, "/sdcard/hcv")
from hcv.stream_bind_probe import run

run("create_link_init")
