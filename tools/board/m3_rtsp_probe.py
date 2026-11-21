"""Open and close an RTSP session after camera and encoder checks pass."""

import sys

sys.path.insert(0, "/sdcard/hcv")
from hcv.stream_probe import run

run("rtsp", use_ap=globals().get("USE_AP", False))
