"""Validate a calibration against the live camera and detector configuration."""

from .config import calibration_fingerprint


def runtime_calibration(calibration, config):
    if calibration.get("schema") != 1 or calibration.get("interp") != "linear":
        raise ValueError("unsupported calibration format")
    if calibration.get("fingerprint") != calibration_fingerprint(config):
        raise ValueError("calibration belongs to another camera configuration")
    roi = config["roi"]
    anchors = calibration["anchors"]
    pixels = [float(anchor["u_px"]) - roi["x"] for anchor in anchors]
    physical = [float(anchor["x_mm"]) for anchor in anchors]
    if len(anchors) < 2 or any(not 0 <= pixel < roi["w"] for pixel in pixels):
        raise ValueError("calibration anchors are outside the detector ROI")
    if any(not -125 <= value <= 125 for value in physical):
        raise ValueError("calibration positions are outside the measurement span")
    if any(a >= b for a, b in zip(pixels, pixels[1:])) or any(
        a >= b for a, b in zip(physical, physical[1:])
    ):
        raise ValueError("calibration anchors must increase on both axes")
    background = calibration.get("bg_profile")
    if background is not None and (
        len(background) != roi["w"]
        or any(type(value) is not int or not 0 <= value <= 255 for value in background)
    ):
        raise ValueError("invalid saved empty-slot background")
    return pixels, physical, background
