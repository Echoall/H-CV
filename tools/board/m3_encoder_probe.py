"""Bind CHN1, read hardware H.264 packets and release the encoder."""

import sys

sys.path.insert(0, "/sdcard/hcv")
from hcv.stream_probe import run

run("encode")
