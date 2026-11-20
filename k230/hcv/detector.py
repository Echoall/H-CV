from .compat import np


def detect_profile(gray, roi=None, ball_diameter_px=20):
    """Detect the largest local deviation from a smooth slot background."""
    image = np.asarray(gray)
    if roi is None:
        y0, x0, height, width = 0, 0, image.shape[0], image.shape[1]
    else:
        x0, y0, width, height = roi
    strip = image[y0:y0 + height, x0:x0 + width]
    profile = np.mean(strip, axis=0)
    kernel_width = max(3, int(ball_diameter_px) * 3)
    if kernel_width > profile.size:
        kernel_width = profile.size
    kernel = np.ones(kernel_width) / kernel_width
    background = np.convolve(profile, kernel, mode="same")
    deviation = np.abs(profile - background)
    edge = kernel_width // 2
    deviation[:edge] = 0
    deviation[-edge:] = 0
    index = int(np.argmax(deviation))
    radius = max(2, int(ball_diameter_px // 2))
    left = max(0, index - radius)
    right = min(profile.size, index + radius + 1)
    weights = deviation[left:right]
    total = np.sum(weights)
    center = index if total <= 0 else np.sum(np.arange(left, right) * weights) / total
    quality = int(min(255, max(0, float(deviation[index]) * 8.0)))
    return {
        "valid": bool(total > 0),
        "u_px": float(x0 + center),
        "v_px": float(y0 + np.argmax(np.abs(strip[:, index] - np.mean(strip[:, index])))),
        "quality": quality,
    }
