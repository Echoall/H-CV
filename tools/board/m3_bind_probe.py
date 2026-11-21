"""Run one selected camera-to-encoder bind probe stage."""

import sys

sys.path.insert(0, "/sdcard/hcv")
from hcv.stream_bind_probe import run

run(globals().get("BIND_STAGE", "chn1"))
