"""Force the real M1 guard to expire before the normal run deadline."""

import sys

sys.path.insert(0, "/sdcard/hcv")
for name in list(sys.modules):
    if name == "main_hcv" or name == "hcv" or name.startswith("hcv."):
        del sys.modules[name]

import main_hcv


start = main_hcv.guard_start
main_hcv.guard_start = lambda _deadline: start(3)
try:
    main_hcv.run(max_runtime_s=15)
except RuntimeError as error:
    if str(error) != "board-side guard deadline exceeded":
        raise
    print("HCV_M1_GUARD_PROBE,deadline_s=3,expired=1,cleanup=completed")
else:
    raise RuntimeError("M1 returned without hitting the guard deadline")
finally:
    main_hcv.guard_start = start
