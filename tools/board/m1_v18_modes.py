"""List advertised GC2093 modes without starting capture or allocating a media session."""
import os
from media.sensor import Sensor

identity = os.uname()
if identity.machine != "k230_canmv_hiwonder" or identity.release.split("-")[0] != "v1.8":
    raise RuntimeError("Expected Hiwonder v1.8")
name, modes = Sensor.list_mode(id=2)
supported = "gc2093" in name.lower() and any(
    mode["width"] == 1280 and mode["height"] == 720 and mode["fps"] == 90 for mode in modes)
print("HCV_M1_SENSOR_MODE", identity.release, name, "1280x720@90", int(supported))
if not supported:
    raise RuntimeError("GC2093 720p90 not advertised")
