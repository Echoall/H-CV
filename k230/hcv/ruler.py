"""Number the long ticks in a horizontal black-on-white ruler strip."""

from math import sqrt

from .compat import np


FIRST_MM = -120
LAST_MM = 120
STEP_MM = 10
MIN_TICKS = 20
MIN_SNR = 4.0


def detect_long_ticks(strip, left_px, right_px):
    """Return numbered subpixel ticks from a fitted 1 cm grid."""
    measurements = measure_long_ticks(np.mean(strip, axis=0), left_px, right_px)
    if len(measurements) < MIN_TICKS:
        raise ValueError("need at least 20 readable long ticks for calibration")
    return [(mm, column) for mm, column, _, _ in measurements]


def _expected_pitch(width, left, right):
    pitch = (right - left) / ((LAST_MM - FIRST_MM) // STEP_MM)
    if not 0 <= left < right < width or pitch < 6:
        raise ValueError("ruler endpoints must be in view, with at least 6 px per centimeter")
    return pitch


def _valley(profile, peak, pitch):
    """Get position, SNR and contrast of one isolated dark line."""
    reach = max(3, int(pitch * 0.38))
    shoulder = max(1, reach // 2)
    if peak < reach or peak + reach >= len(profile):
        return None
    if profile[peak] >= profile[peak - 1] or profile[peak] > profile[peak + 1]:
        return None
    left = profile[peak - reach:peak - reach + shoulder]
    right = profile[peak + reach - shoulder + 1:peak + reach + 1]
    left_level = float(np.median(left))
    right_level = float(np.median(right))
    slope = (right_level - left_level) / (2 * reach - shoulder + 1)
    level = min(left_level, right_level)
    depth = level - float(profile[peak])
    if depth <= 0:
        return None
    threshold = level - depth / 2
    first = last = peak
    while first > peak - reach and profile[first - 1] < threshold:
        first -= 1
    while last < peak + reach and profile[last + 1] < threshold:
        last += 1
    if first == peak - reach or last == peak + reach or last - first + 1 > max(2, int(pitch / 4)):
        return None
    # Use the black line shape and one shoulder pixel on each side.
    midpoint_level = (left_level + right_level) / 2
    mass = moment = 0.0
    for col in range(first - 1, last + 2):
        weight = max(0.0, midpoint_level + slope * (col - peak) - float(profile[col]))
        mass += weight
        moment += col * weight
    # Estimate spatial noise from the detrended background, with a
    # one-gray-level floor for quantized profiles.
    variance = 0.0
    mean_offset = (shoulder - 1) / 2
    for index in range(shoulder):
        offset = index - mean_offset
        variance += (float(left[index]) - left_level - slope * offset) ** 2
        variance += (float(right[index]) - right_level - slope * offset) ** 2
    noise = max(1.0, sqrt(variance / max(1, 2 * shoulder - 2)))
    snr = depth / noise
    return (moment / mass, snr, depth) if snr >= MIN_SNR else None


def _nearest(valleys, column, radius):
    """Return the closest plausible valley, or None if the local window is empty."""
    first = round(column) - int(radius) - 1
    last = round(column) + int(radius) + 2
    nearest = None
    distance = radius
    for peak in range(first, last):
        valley = valleys.get(peak)
        if valley is not None and abs(valley[0] - column) <= distance:
            nearest = valley
            distance = abs(valley[0] - column)
    return nearest


def measure_long_ticks(profile, left_px, right_px):
    """Fit one grid and return (mm, column, SNR, residual in pixels) per tick."""
    pitch = _expected_pitch(len(profile), left_px, right_px)
    cols = range(max(3, int(left_px - pitch)), min(len(profile) - 3, int(right_px + pitch) + 1))
    valleys = {}
    for peak in cols:
        valley = _valley(profile, peak, pitch)
        if valley is not None:
            valleys[peak] = valley

    count = (LAST_MM - FIRST_MM) // STEP_MM + 1
    # Search offsets at both manual endpoints. Allow for perspective and
    # endpoint rounding without reaching the half-centimeter phase.
    shifts = [index * pitch / 16 for index in range(-4, 5)]
    offsets = [index / (count - 1) for index in range(count)]
    best_score = -1.0
    for start_shift in shifts:
        for end_shift in shifts:
            start = left_px + start_shift
            span = right_px + end_shift - start
            for bow in (-pitch / 6, -pitch / 12, 0, pitch / 12, pitch / 6):
                grid = [start + span * part + 4 * bow * part * (1 - part) for part in offsets]
                score = 0.0
                for center in grid:
                    valley = _nearest(valleys, center, 0.20 * pitch)
                    if valley is not None:
                        score += 1 - 0.5 * (abs(valley[0] - center) / (0.20 * pitch)) ** 2
                if score > best_score:
                    best_score, predicted = score, grid

    # A dense train whose midpoint phase is equally strong cannot identify
    # which of the two interleaved phases contains the long ticks.
    radius = min(2.0, pitch * 0.16)
    matches = [_nearest(valleys, center, radius) for center in predicted]
    midpoint = [_nearest(valleys, (a + b) / 2, radius) for a, b in zip(predicted, predicted[1:])]
    depths = [match[2] for match in matches if match is not None]
    alternate_depths = [match[2] for match in midpoint if match is not None]
    if not depths:
        return []
    median_depth = float(np.median(np.array(depths)))
    if (len(alternate_depths) * 3 >= 2 * (count - 1)
            and float(np.median(np.array(alternate_depths))) >= 0.8 * median_depth):
        return []
    # An unusually deep line that also misses the finer grid is more likely
    # a seam or a shadow edge than a printed tick.
    return [
        (FIRST_MM + index * STEP_MM, match[0], match[1], match[0] - center)
        for index, (center, match) in enumerate(zip(predicted, matches)) if match is not None
        and (abs(match[0] - center) <= 0.75 or match[2] <= 3 * median_depth)
    ]


def _long_tick_candidates(profile, left_px, right_px, min_depth):
    width = len(profile)
    pitch = _expected_pitch(width, left_px, right_px)
    radius = int(0.4 * pitch)
    limit = max(2, int(pitch * 0.25))
    ticks = []
    for index, mm in enumerate(range(FIRST_MM, LAST_MM + STEP_MM, STEP_MM)):
        guess = int(round(left_px + index * pitch))
        start = max(0, guess - radius)
        stop = min(width, guess + radius + 1)
        flank = min(3, (stop - start - 1) // 3)
        if flank == 0:
            continue
        baseline = min(
            float(np.median(profile[start:start + flank])),
            float(np.median(profile[stop - flank:stop])),
        )
        peak = start + int(np.argmin(profile[start:stop]))
        depth = baseline - float(profile[peak])
        if depth <= 0 or depth < min_depth * baseline:
            continue
        threshold = baseline - depth * 0.5
        left = right = peak
        while left > start and profile[left - 1] <= threshold:
            left -= 1
        while right + 1 < stop and profile[right + 1] <= threshold:
            right += 1
        if left == start or right == stop - 1 or right - left + 1 > limit:
            continue
        mass = moment = 0.0
        # Subpixel lines can put less than half the peak contrast in a
        # neighboring column. Include their shoulders in the centroid.
        for col in range(max(start, left - 1), min(stop, right + 2)):
            weight = max(0.0, baseline - float(profile[col]))
            mass += weight
            moment += col * weight
        ticks.append((mm, moment / mass))

    return ticks, pitch
