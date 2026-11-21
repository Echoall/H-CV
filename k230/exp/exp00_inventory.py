"""P0-EXP00: inventory available APIs without starting the camera or networking."""

import gc
import json
import os
import sys
import time


MODULES = (
    "media.sensor", "media.media", "media.display", "media.vencoder",
    "media.rtspserver", "multimedia", "ulab", "ulab.numpy", "image", "cv2",
    "cv_lite", "nncase_runtime", "network", "socket", "_thread", "machine",
    "hashlib", "binascii",
)
OBJECTS = (
    ("media.sensor", "Sensor"), ("media.media", "MediaManager"),
    ("media.display", "Display"), ("media.vencoder", "Encoder"),
    ("media.rtspserver", "RtspServer"), ("multimedia", "rtsp_server"),
    ("multimedia", "k_rtsp_server"), ("image", "Image"), ("machine", "WDT"),
    ("machine", "UART"), ("machine", "FPIOA"), ("machine", "Pin"),
    ("network", "WLAN"),
)


def inventory():
    cutoff = time.ticks_add(time.ticks_ms(), 20_000)
    if hasattr(os, "EXITPOINT_ENABLE"):
        os.exitpoint(os.EXITPOINT_ENABLE)

    def checkpoint():
        if hasattr(os, "exitpoint"):
            os.exitpoint()
        if time.ticks_diff(cutoff, time.ticks_ms()) <= 0:
            raise RuntimeError("inventory exceeded its board-side time budget")

    gc.collect()
    data = {
        "os_uname": str(os.uname()) if hasattr(os, "uname") else None,
        "sys_implementation": str(sys.implementation),
        "sys_version": sys.version,
        "mem_free_before_imports": gc.mem_free(),
        "modules": {},
        "objects": {},
    }
    imported = {}
    for name in MODULES:
        checkpoint()
        try:
            module = __import__(name, None, None, ("*",))
            imported[name] = module
            data["modules"][name] = {
                "available": True,
                "version": str(getattr(module, "__version__", "")),
                "api": dir(module),
            }
        except Exception as error:
            data["modules"][name] = {"available": False, "error": repr(error)}

    for module, name in OBJECTS:
        checkpoint()
        obj = getattr(imported.get(module), name, None)
        data["objects"][module + "." + name] = dir(obj) if obj is not None else None

    numpy = imported.get("ulab.numpy")
    if numpy is not None:
        try:
            data["ulab_mean_axis"] = str(numpy.mean(numpy.array([[1, 2], [3, 4]]), axis=0))
        except Exception as error:
            data["ulab_mean_axis_error"] = repr(error)
    gc.collect()
    data["mem_free_after_imports"] = gc.mem_free()
    return data


print("HCV_EXP00 " + json.dumps(inventory()))
