"""Run the 610 s M1 baseline with its archived configuration on Hiwonder v1.8."""
import os
import sys

identity = os.uname()
if identity.machine != "k230_canmv_hiwonder" or identity.release.split("-")[0] != "v1.8":
    raise RuntimeError("Expected Hiwonder v1.8")
sys.path.insert(0, "/sdcard/hcv")
for name in list(sys.modules):
    if name in ("main_hcv", "hcv") or name.startswith("hcv."):
        del sys.modules[name]
from hcv.media_session_guard import claim

try:
    os.stat("/sdcard/hcv/run")
except OSError:
    os.mkdir("/sdcard/hcv/run")
claim()
from main_hcv import run

run(max_runtime_s=610, config_directory="/sdcard/hcv/m1_v18",
    calibration_path="/sdcard/hcv/calib.json", report_capture_span=True)
print("HCV_M1_V18_RETURNED")
