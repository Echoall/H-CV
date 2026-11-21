"""One-dimensional projective compensation and calibration helpers."""

import math


def _as_values(values):
    return [float(value) for value in values]


def fit_projective(x_values, y_values):
    """Fit ``y = (a*x + b) / (c*x + 1)`` through three or more points."""
    x_values = _as_values(x_values)
    y_values = _as_values(y_values)
    if len(x_values) != len(y_values) or len(x_values) < 3:
        raise ValueError("projective fit requires at least three pairs")
    if len(set(x_values)) < 3 or len(set(y_values)) < 3:
        raise ValueError("projective points must be distinct")
    matrix = []
    rhs = []
    for x, y in zip(x_values, y_values):
        matrix.append([x, 1.0, -x * y])
        rhs.append(y)
    for pivot in range(3):
        largest = pivot
        for row in range(pivot + 1, 3):
            if abs(matrix[row][pivot]) > abs(matrix[largest][pivot]):
                largest = row
        scale = max(abs(value) for value in matrix[largest])
        if scale == 0 or abs(matrix[largest][pivot]) <= 1e-10 * scale:
            raise ValueError("projective equation is ill-conditioned")
        if largest != pivot:
            matrix[pivot], matrix[largest] = matrix[largest], matrix[pivot]
            rhs[pivot], rhs[largest] = rhs[largest], rhs[pivot]
        divisor = matrix[pivot][pivot]
        for column in range(pivot, 3):
            matrix[pivot][column] /= divisor
        rhs[pivot] /= divisor
        for row in range(3):
            if row == pivot:
                continue
            factor = matrix[row][pivot]
            for column in range(pivot, 3):
                matrix[row][column] -= factor * matrix[pivot][column]
            rhs[row] -= factor * rhs[pivot]
    coefficients = (rhs[0], rhs[1], rhs[2])
    for x in x_values:
        if abs(coefficients[2] * x + 1.0) <= 1e-8:
            raise ValueError("projective denominator is singular")
    return coefficients


def fit_similarity(x_values, y_values):
    """Fit the two-point fallback ``y = scale*x + offset``."""
    x_values = _as_values(x_values)
    y_values = _as_values(y_values)
    if len(x_values) != len(y_values) or len(x_values) < 2:
        raise ValueError("similarity fit requires at least two pairs")
    denominator = x_values[-1] - x_values[0]
    if abs(denominator) <= 1e-8:
        raise ValueError("similarity points are coincident")
    scale = (y_values[-1] - y_values[0]) / denominator
    offset = y_values[0] - scale * x_values[0]
    return scale, offset


def apply_projective(x_value, coefficients):
    a, b, c = coefficients
    denominator = c * float(x_value) + 1.0
    if abs(denominator) <= 1e-8:
        raise ValueError("projective denominator is singular")
    return (a * float(x_value) + b) / denominator


def apply_similarity(x_value, coefficients):
    scale, offset = coefficients
    return scale * float(x_value) + offset


def _project_pin_hole(s_value, theta, model):
    height = float(model["height_mm"])
    scale = float(model["scale_px_per_mm"])
    center = float(model["center_u"])
    pivot = float(model.get("pivot_mm", -125.0))
    distance = float(s_value) - pivot
    x = pivot + distance * math.cos(theta)
    z = distance * math.sin(theta)
    return center + scale * height * x / (height - z)


def _inverse_pin_hole(u_value, theta, model):
    height = float(model["height_mm"])
    scale = float(model["scale_px_per_mm"])
    center = float(model["center_u"])
    pivot = float(model.get("pivot_mm", -125.0))
    ratio = (float(u_value) - center) / (scale * height)
    distance = (
        ratio * height - pivot
    ) / (math.cos(theta) + ratio * math.sin(theta))
    return pivot + distance


def _estimate_tilt(current, positions, model):
    candidates = []
    height = float(model["height_mm"])
    scale = float(model["scale_px_per_mm"])
    center = float(model["center_u"])
    pivot = float(model.get("pivot_mm", -125.0))
    for u_value, s_value in zip(current, positions):
        distance = float(s_value) - pivot
        ratio = (float(u_value) - center) / (scale * height)
        argument = (
            ratio * height - pivot
        ) / (distance * math.sqrt(1.0 + ratio * ratio))
        if abs(argument) > 1.0:
            continue
        phase = math.atan(ratio)
        delta = math.acos(max(-1.0, min(1.0, argument)))
        candidates.extend((phase + delta, phase - delta))
    valid = [value for value in candidates if -math.radians(30) <= value <= math.radians(30)]
    if not valid:
        raise ValueError("cannot estimate tilt from marker geometry")
    best = min(
        valid,
        key=lambda theta: sum(
            (_project_pin_hole(s, theta, model) - u) ** 2
            for u, s in zip(current, positions)
        ),
    )
    return best


def compensate(u_value, current_u, reference_u, model=None):
    """Map a current ball position into the reference image coordinate.

    Returns ``(mapped_u, mode, coefficients)``.  The mode is ``FULL`` for a
    three-marker projective fit, ``DEGRADED`` for a two-marker similarity
    fit, and ``NONE`` when there are too few usable markers.
    """
    pairs = [
        (float(current), float(reference))
        for current, reference in zip(current_u, reference_u)
        if current is not None and reference is not None
    ]
    unique = []
    seen = set()
    for pair in pairs:
        if pair[0] not in seen:
            unique.append(pair)
            seen.add(pair[0])
    pairs = unique
    if len(pairs) >= 3:
        try:
            coefficients = fit_projective(
                [pair[0] for pair in pairs],
                [pair[1] for pair in pairs],
            )
            return apply_projective(u_value, coefficients), "FULL", coefficients
        except ValueError:
            pass
    if len(pairs) >= 2:
        if model is not None and "marker_s_mm" in model:
            marker_s = [float(value) for value in model["marker_s_mm"]]
            usable = []
            for current, reference in pairs:
                index = min(
                    range(len(reference_u)),
                    key=lambda item: abs(float(reference_u[item]) - reference),
                )
                usable.append((current, marker_s[index]))
            theta = _estimate_tilt(
                [pair[0] for pair in usable],
                [pair[1] for pair in usable],
                model,
            )
            recovered_s = _inverse_pin_hole(u_value, theta, model)
            mapped = interpolate_lut(
                recovered_s, marker_s, [float(value) for value in reference_u]
            )
            return mapped, "GEOMETRIC", (theta, recovered_s)
        coefficients = fit_similarity(
            [pairs[0][0], pairs[1][0]],
            [pairs[0][1], pairs[1][1]],
        )
        return apply_similarity(u_value, coefficients), "DEGRADED", coefficients
    return None, "NONE", None


def interpolate_lut(u_value, pixel_values, physical_values):
    """Piecewise-linear calibration lookup with bounded extrapolation."""
    pixels = _as_values(pixel_values)
    physical = _as_values(physical_values)
    if len(pixels) != len(physical) or len(pixels) < 2:
        raise ValueError("calibration table needs at least two points")
    if any(pixels[index] >= pixels[index + 1] for index in range(len(pixels) - 1)):
        raise ValueError("calibration pixels must be strictly increasing")
    value = float(u_value)
    if value <= pixels[0]:
        index = 0
    elif value >= pixels[-1]:
        index = len(pixels) - 2
    else:
        index = 0
        while not (pixels[index] <= value <= pixels[index + 1]):
            index += 1
    fraction = (value - pixels[index]) / (pixels[index + 1] - pixels[index])
    return physical[index] + fraction * (physical[index + 1] - physical[index])


def compensation_quality(mode):
    """Quality multiplier for a detector result after marker compensation."""
    if mode == "FULL":
        return 1.0
    if mode == "GEOMETRIC":
        return 0.75
    if mode == "DEGRADED":
        return 0.25
    return 0.0
