import sys

sys.path.append("/sdcard/hcv")
sys.path.append("/sdcard/hcv/exp")

if "cap_frames" in sys.modules:
    del sys.modules["cap_frames"]
from cap_frames import capture

print("P1_T01_BEGIN")
capture(
    mode="single",
    count=3,
    output_dir="/sdcard/hcv/p1_t01_single2",
    capture_chn1=True,
    deadline_s=45,
)
print("P1_T01_SINGLE_DONE")
capture(
    mode="sequence",
    count=6,
    output_dir="/sdcard/hcv/p1_t01_sequence_full3",
    capture_chn1=False,
    deadline_s=90,
)
print("P1_T01_FULL_SEQUENCE_DONE")
capture(
    mode="sequence",
    count=30,
    output_dir="/sdcard/hcv/p1_t01_sequence_roi3",
    capture_chn1=False,
    roi_y=148,
    roi_height=64,
    deadline_s=120,
)
print("P1_T01_ROI_SEQUENCE_DONE")
