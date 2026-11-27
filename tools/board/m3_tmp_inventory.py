"""Inspect the root filesystem and /tmp without starting media hardware."""

import json
import os
import time


result = {
    "root": os.listdir("/"),
    "tmp_exists": False,
    "tmp_writable": False,
}
try:
    os.stat("/tmp")
    result["tmp_exists"] = True
except OSError:
    print("HCV_TMP " + json.dumps(result))
else:
    probe = "/tmp/hcv-tmp-write-check"
    try:
        with open(probe, "w") as handle:
            handle.write(str(time.ticks_us()))
        result["tmp_writable"] = True
    except OSError:
        pass
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass
    print("HCV_TMP " + json.dumps(result))
