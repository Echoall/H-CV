"""Configure the K230 AP and run the configuration-driven CHN0 stream."""

import sys
import network

sys.path.insert(0, "/sdcard/hcv")

from hcv.config import load_config
from hcv.ch0_configurable_probe import run


config = load_config("/sdcard/hcv")
wifi = config["wifi"]
if wifi["mode"] != "ap":
    raise ValueError("M3 configurable probe requires wifi.mode=ap")

ap = network.WLAN(network.AP_IF)
try:
    ap.config(
        ssid=wifi["ssid"],
        key=wifi["password"],
        channel=wifi["channel"],
    )
except TypeError:
    print("HCV_AP_CHANNEL_UNSUPPORTED")
    ap.config(ssid=wifi["ssid"], key=wifi["password"])

print("HCV_AP_READY")
run()
