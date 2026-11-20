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
    output_dir="/sdcard/hcv/p1_t01_sequence2",
    capture_chn1=False,
    deadline_s=90,
)
print("P1_T01_SEQUENCE_DONE")
