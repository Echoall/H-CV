"""Configure the K230 AP and run the configuration-driven CHN0 stream."""

import sys

import network

sys.path.insert(0, "/sdcard/hcv")

from hcv.config import _merge, _validate, load_config
from hcv.ch0_configurable_probe import run


config = load_config("/sdcard/hcv")
if "HCV_CONFIG_OVERRIDE" in globals():
    _merge(config, HCV_CONFIG_OVERRIDE)
    _validate(config)
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

if "HCV_STREAM_SECONDS" in globals():
    address = ap.ifconfig()[0]
    if address == "0.0.0.0":
        raise RuntimeError("HCV_AP_NO_ADDRESS")
    print("HCV_AP_READY")
    print("HCV_RTSP_PRIVATE,%s,%d,%s" % (
        address, config["stream"]["port"], config["stream"]["path"]
    ))
    vicap_limit = globals().get("HCV_VICAP_STREAM_LIMIT", False)
    if vicap_limit and not globals().get("HCV_DIRECT_RTSP", False):
        from hcv.tcp_control import send_control

        try:
            send_control(address, config["stream"]["port"])
        except OSError as error:
            print("HCV_TCP_CONTROL_ERROR", error)
        finally:
            print("HCV_TCP_CONTROL_CLOSED")
    run(seconds=HCV_STREAM_SECONDS, announce_url=False, config=config,
        use_firmware_encoder=globals().get("HCV_USE_FIRMWARE_ENCODER", False),
        vicap_stream_limit=vicap_limit, raw_clip_path=globals().get("HCV_RAW_CLIP_PATH"))
else:
    print("HCV_AP_READY")
    run()
