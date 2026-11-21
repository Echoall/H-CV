import sys

sys.path.append("/sdcard/hcv")
for name in list(sys.modules):
    if name == "main_hcv" or name == "hcv" or name.startswith("hcv."):
        del sys.modules[name]
from main_hcv import run

run(max_runtime_s=15)
