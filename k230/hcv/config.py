"""Read ICD v1.0.1 configuration on CPython and CanMV."""

import errno
import json
import os


_FIELDS = {
    "schema": int,
    "sensor": {
        "id": int, "width": int, "height": int, "fps": int,
        "hmirror": bool, "vflip": bool,
    },
    "chn_detect": {"width": int, "height": int, "format": str, "crop": (list, type(None))},
    "chn_stream": {"width": int, "height": int, "format": str},
    "isp": {
        "ae_lock": bool, "exposure_us": (int, float, type(None)), "gain": (int, float, type(None)),
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
        if optional:
            os.stat(path)
        with open(path, "r") as source:
            config = json.load(source)
    except OSError as error:
        if optional and error.args and error.args[0] == errno.ENOENT:
            return {}
        raise
    except ValueError:
        raise ValueError("invalid JSON file: " + path) from None
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
    for key in config:
        if key not in fields:
            raise ValueError("unknown key " + prefix + key)
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


def _require(condition, field):
    if not condition:
        raise ValueError("invalid value for " + field)


def _inside(box, width, height):
    x, y, w, h = box
    return x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= width and y + h <= height


def _validate(config):
    _validate_fields(config, _FIELDS)
    _require(config["schema"] == 1, "schema")
    sensor = config["sensor"]
    width, height, fps = sensor["width"], sensor["height"], sensor["fps"]
    _require(sensor["id"] in (0, 1, 2), "sensor.id")
    _require((width, height, fps) in (
        (1920, 1080, 30), (1920, 1080, 60), (1280, 960, 60), (1280, 720, 90),
    ), "sensor.width/height/fps")

    detect = config["chn_detect"]
    stream_channel = config["chn_stream"]
    for name, channel in (("chn_detect", detect), ("chn_stream", stream_channel)):
        w, h = channel["width"], channel["height"]
        _require(0 < w <= width and w % 16 == 0, name + ".width")
        _require(0 < h <= height and h % 2 == 0, name + ".height")
    _require(detect["format"] in ("GRAYSCALE", "RGB565", "RGB888", "YUV420SP"),
             "chn_detect.format")
    _require(stream_channel["format"] == "YUV420SP", "chn_stream.format")
    crop = detect["crop"]
    if crop is not None:
        _require(len(crop) == 4 and all(type(value) is int for value in crop)
                 and _inside(crop, width, height), "chn_detect.crop")
    roi = config["roi"]
    _require(_inside((roi["x"], roi["y"], roi["w"], roi["h"]),
                     detect["width"], detect["height"]), "roi")

    for field, value in (("isp.exposure_us", config["isp"]["exposure_us"]),
                         ("isp.gain", config["isp"]["gain"])):
        _require(value is None or 0 < value < float("inf"), field)

    markers = config["markers"]
    positions = markers["x_mm"]
    _require(len(positions) == 3 and all(type(value) in (int, float) for value in positions)
             and -125 <= positions[0] < positions[1] < positions[2] <= 125, "markers.x_mm")
    _require(markers["polarity"] in ("dark", "bright"), "markers.polarity")
    _require(config["detector"]["name"] in ("blob", "profile"), "detector.name")

    tracker = config["tracker"]
    for name in ("q_accel", "r_meas", "gate_mm"):
        _require(0 < tracker[name] < float("inf"), "tracker." + name)
    for name in ("coast_max", "reacquire_frames"):
        _require(1 <= tracker[name] <= 10, "tracker." + name)

    uart = config["uart"]
    _require(uart["id"] in (1, 2, 3, 4), "uart.id")
    _require((uart["id"], uart["tx_pin"], uart["rx_pin"]) in (
        (1, 3, 4), (1, 40, 41), (2, 11, 12), (2, 5, 6), (3, 50, 51), (4, 36, 37),
    ), "uart.id/tx_pin/rx_pin")
    _require(uart["baud"] in (115200, 230400, 460800, 921600), "uart.baud")
    _require(0.5 <= config["status_hz"] <= 10, "status_hz")
    _require(0 <= config["latency_const_ms"] <= 255, "latency_const_ms")

    wifi = config["wifi"]
    _require(wifi["mode"] in ("ap", "sta", "off"), "wifi.mode")
    _require(1 <= wifi["channel"] <= 13, "wifi.channel")
    if wifi["mode"] != "off":
        _require(wifi["password"] != "SEE_LOCAL" and 8 <= len(wifi["password"]) <= 63,
                 "wifi.password (value: ***)")

    stream = config["stream"]
    telemetry = config["telemetry"]
    _require(0 < stream["fps"] <= fps, "stream.fps")
    ports = (("stream.port", stream["port"]),
             ("telemetry.hello_port", telemetry["hello_port"]),
             ("telemetry.data_port", telemetry["data_port"]))
    seen = {}
    for field, value in ports:
        _require(1 <= value <= 65535, field)
        _require(value not in seen, field + " (conflicts with " + seen.get(value, "") + ")")
        seen[value] = field
    _require(config["log"]["level"] in ("debug", "info", "warn", "error"), "log.level")


def format_config_for_log(config):
    """Return a JSON string safe for logs without modifying the config."""
    redacted = dict(config)
    if "wifi" in redacted:
        redacted["wifi"] = dict(redacted["wifi"])
        if "password" in redacted["wifi"]:
            redacted["wifi"]["password"] = "***"
    return json.dumps(redacted)


def load_config(directory="."):
    """Read config.json, overlay local fields, validate and return the result."""
    prefix = str(directory).rstrip("/") + "/"
    config = _read(prefix + "config.json")
    _merge(config, _read(prefix + "config.local.json", optional=True))
    _validate(config)
    return config
