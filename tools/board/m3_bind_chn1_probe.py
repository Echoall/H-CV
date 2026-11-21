"""Probe CHN1 configuration only."""

import sys

sys.path.insert(0, "/sdcard/hcv")
from hcv.stream_bind_probe import run

run("chn1")
