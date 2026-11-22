"""Associate with the configured Wi-Fi and stream CHN0 once."""

import sys
import time

import network

sys.path.insert(0, "/sdcard/hcv")

from hcv.config import load_config
from hcv.guard import start, tick


config = load_config("/sdcard/hcv")
wifi = config["wifi"]
if wifi["mode"] != "sta":
    raise ValueError("M3 station probe requires wifi.mode=sta")
start(20)
station = network.WLAN(network.STA_IF)
station.connect(wifi["ssid"], wifi["password"])
deadline = time.ticks_add(time.ticks_ms(), 16_000)
while time.ticks_diff(deadline, time.ticks_ms()) > 0:
    tick()
    address = station.ifconfig()[0] if station.isconnected() else "0.0.0.0"
    if address != "0.0.0.0":
        break
    time.sleep_ms(250)
else:
    raise RuntimeError("HCV_STA_NO_ASSOCIATION")

# Consumed by the host over USB. Do not save this line in the evidence log.
print("HCV_RTSP_PRIVATE,%s,%d,%s" % (address, config["stream"]["port"], config["stream"]["path"]))
for name in ("hcv.ch0_configurable_probe", "hcv.media_session_guard", "hcv.stream"):
    sys.modules.pop(name, None)
from hcv.ch0_configurable_probe import run

run(seconds=60, announce_url=False)
