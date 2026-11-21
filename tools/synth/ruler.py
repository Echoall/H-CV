"""Generate a horizontal paper ruler with known projected subpixel centers."""

import numpy as np

from .generate import project_s_to_u


def generate_ruler(line_width_mm, obscured_mm=(), seed=0, center_px=320.25,
                   scale_px_per_mm=1.7):
    width, height, supersampling = 640, 360, 32
    x = (np.arange(width * supersampling) + 0.5) / supersampling - 0.5
    ground = 224 + 0.04 * (x - center_px)
    long_ink = np.zeros_like(x)
    short_ink = np.zeros_like(x)
    positions = np.arange(-120, 121)
    projected = project_s_to_u(
        positions, theta_deg=0, scale_px_per_mm=scale_px_per_mm, center_u=center_px,
    )
    truth = {}
    for mm, center in zip(positions, projected):
        if mm % 10 == 0:
            truth[int(mm)] = float(center)
            if mm in obscured_mm:
                continue
            ink, thickness = long_ink, line_width_mm
        else:
            ink, thickness = short_ink, 0.15
        ink[np.abs(x - center) <= thickness * scale_px_per_mm / 2] = 190
    # Integrate a blurred image over each sensor pixel. A narrow tick can
    # otherwise be entirely contained in one pixel with no subpixel signal.
    distances = np.arange(-2 * supersampling, 2 * supersampling + 1) / supersampling
    blur = np.exp(-(distances / 0.55)**2 / 2)
    blur /= blur.sum()

    def raster(ink):
        blurred = np.convolve(ink, blur, mode="same")
        return (ground - blurred).reshape(width, supersampling).mean(axis=1)

    rng = np.random.default_rng(seed)
    image = np.tile(raster(np.zeros_like(x)), (height, 1))
    image[90:101] = raster(long_ink + short_ink)
    image[101:112] = raster(long_ink)
    columns = np.arange(width)
    for mm in obscured_mm:
        center = truth[mm]
        radius = 3.2 * scale_px_per_mm
        image[101:112] -= 55 * np.exp(-((columns - center) / radius) ** 2)
    for center in truth.values():
        column = int(center)
        image[115:119, column - 2:column + 3] -= 70
    image += rng.normal(0, 1.8, image.shape)
    return np.clip(image, 0, 255).astype(np.uint8), truth
