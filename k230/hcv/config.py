"""Read ICD v1.0 configuration on CPython and CanMV."""

import errno
import json


_FIELDS = {
    "schema": int,
    "sensor": {
        "id": int, "width": int, "height": int, "fps": int,
        "hmirror": bool, "vflip": bool,
    },
    "chn_detect": {"width": int, "height": int, "format": str, "crop": (list, type(None))},
    "chn_stream": {"width": int, "height": int, "format": str},
    "isp": {
        "ae_lock": bool, "exposure_us": int, "gain": (int, float, type(None)),
        "awb_lock": bool,
    },
    "roi": {"x": int, "y": int, "w": int, "h": int},
    "markers": {"enabled": bool, "x_mm": list, "search_px": int, "polarity": str},
    "detector": {"name": str, "params": dict},
    "tracker": {
        "q_accel": (int, float), "r_meas": (int, float), "gate_mm": (int, float),
        "coast_max": int, "reacquire_frames": int,
    },
    "uart": {"id": int, "tx_pin": int, "rx_pin": int, "baud": int},
    "status_hz": (int, float),
    "latency_const_ms": int,
    "wifi": {"mode": str, "ssid": str, "channel": int, "password": str},
    "stream": {
        "enabled": bool, "port": int, "path": str, "fps": int,
        "bitrate_kbps": int, "gop": int,
    },
    "telemetry": {"enabled": bool, "hello_port": int, "data_port": int, "batch_ms": int},
    "log": {"level": str, "run_csv": bool},
    "safe_boot": {"button_gpio": (int, type(None)), "skip_file": str},
}


def _read(path, optional=False):
    try:
        with open(path, "r") as source:
            config = json.load(source)
    except OSError as error:
        if optional and error.args and error.args[0] == errno.ENOENT:
            return {}
        raise
    if not isinstance(config, dict):
        raise ValueError("configuration must be a JSON object: " + path)
    return config


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def _validate_fields(config, fields, prefix=""):
    for key, rule in fields.items():
        path = prefix + key
        if key not in config:
            raise ValueError("missing field " + path)
        value = config[key]
        if isinstance(rule, dict):
            if not isinstance(value, dict):
                raise ValueError("expected object at " + path)
            _validate_fields(value, rule, path + ".")
        elif type(value) not in (rule if isinstance(rule, tuple) else (rule,)):
            raise ValueError("invalid type for " + path)


def _validate(config):
    _validate_fields(config, _FIELDS)
    if config["schema"] != 1:
        raise ValueError("unsupported config schema")
    if config["uart"]["baud"] not in (115200, 230400, 460800, 921600):
        raise ValueError("unsupported uart.baud")
    for channel in ("sensor", "chn_detect", "chn_stream"):
        if min(config[channel]["width"], config[channel]["height"]) < 1:
            raise ValueError(channel + " dimensions must be positive")

    roi = config["roi"]
    detect = config["chn_detect"]
    if (min(roi["x"], roi["y"]) < 0 or min(roi["w"], roi["h"]) < 1
            or roi["x"] + roi["w"] > detect["width"]
            or roi["y"] + roi["h"] > detect["height"]):
        raise ValueError("roi must fit inside chn_detect")

    crop = detect["crop"]
    if crop is not None:
        if (len(crop) != 4 or any(type(value) is not int for value in crop)
                or min(crop[:2]) < 0 or min(crop[2:]) < 1):
            raise ValueError("chn_detect.crop must be [x, y, width, height] or null")
    markers = config["markers"]["x_mm"]
    if (len(markers) != 3 or any(type(value) not in (int, float) for value in markers)
            or not markers[0] < markers[1] < markers[2]):
        raise ValueError("markers.x_mm must contain three increasing positions")
    for section, key in (("stream", "port"), ("telemetry", "hello_port"),
                         ("telemetry", "data_port")):
        if not 1 <= config[section][key] <= 65535:
            raise ValueError(section + "." + key + " must be a valid port")


def load_config(directory="."):
    """Read config.json, overlay local fields, validate and return the result."""
    prefix = str(directory).rstrip("/") + "/"
    config = _read(prefix + "config.json")
    _merge(config, _read(prefix + "config.local.json", optional=True))
    _validate(config)
    return config
