"""Stream CHN0 once over an already associated K230 station interface."""

import sys

import network

sys.path.insert(0, "/sdcard/hcv")
for name in ("hcv.ch0_configurable_probe", "hcv.media_session_guard", "hcv.stream"):
    sys.modules.pop(name, None)

from hcv.ch0_configurable_probe import run
from hcv.config import load_config


config = load_config("/sdcard/hcv")
station = network.WLAN(network.STA_IF)
if config["wifi"]["mode"] != "sta" or not station.isconnected():
    raise RuntimeError("HCV_STA_NOT_CONNECTED")
address = station.ifconfig()[0]
if address == "0.0.0.0":
    raise RuntimeError("HCV_STA_NO_ADDRESS")

# Consumed by the host over USB. Do not save this line in the evidence log.
print("HCV_RTSP_PRIVATE,%s,%d,%s" % (address, config["stream"]["port"], config["stream"]["path"]))
run(seconds=65, announce_url=False)
