"""Number the long ticks in a horizontal black-on-white ruler strip."""

from .compat import np


FIRST_MM = -120
LAST_MM = 120
STEP_MM = 10


def detect_long_ticks(strip, left_px, right_px, min_depth=0.10):
    """Return numbered subpixel ticks using the approximate ±120 mm endpoints.

    ``strip`` contains only the ends of the long ticks. All column coordinates
    are relative to that strip. The caller supplies the two physical endpoints
    so a missing tick cannot shift the numbering.
    """
    profile = np.mean(strip, axis=0)
    ticks, pitch = _long_tick_candidates(profile, left_px, right_px, min_depth)
    if len(ticks) < 20:
        raise ValueError("need at least 20 readable long ticks for calibration")
    for (mm0, u0), (mm1, u1) in zip(ticks, ticks[1:]):
        spacing = (u1 - u0) / ((mm1 - mm0) / STEP_MM)
        if not 0.65 * pitch <= spacing <= 1.35 * pitch:
            raise ValueError("long tick spacing is inconsistent with the numbered ruler")
    return ticks


def _long_tick_candidates(profile, left_px, right_px, min_depth):
    width = len(profile)
    pitch = (right_px - left_px) / ((LAST_MM - FIRST_MM) // STEP_MM)
    if not 0 <= left_px < right_px < width or pitch < 6:
        raise ValueError("ruler endpoints must be in view, with at least 6 px per centimeter")
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
