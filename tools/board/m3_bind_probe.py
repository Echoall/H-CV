"""Run one selected camera-to-encoder bind probe stage."""

import sys

sys.path.insert(0, "/sdcard/hcv")
sys.modules.pop("hcv.stream_bind_probe", None)
from hcv.stream_bind_probe import run

run(globals().get("BIND_STAGE", "chn1"))
