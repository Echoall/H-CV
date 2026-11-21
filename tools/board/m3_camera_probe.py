"""Start and stop both camera channels without binding the encoder."""

import sys

sys.path.insert(0, "/sdcard/hcv")
from hcv.stream_probe import run

run("camera")
