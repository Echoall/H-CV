"""Run the guarded official-order CHN0 encoder and RTSP probe."""

import sys

sys.path.insert(0, "/sdcard/hcv")
sys.modules.pop("hcv.ch0_rtsp_probe", None)
from hcv.ch0_rtsp_probe import run

run()
