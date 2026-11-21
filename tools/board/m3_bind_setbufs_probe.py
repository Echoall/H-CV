"""Probe CHN1 configuration and encoder output buffers."""

import sys

sys.path.insert(0, "/sdcard/hcv")
from hcv.stream_bind_probe import run

run("setbufs")
