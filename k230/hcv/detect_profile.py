"""Vectorised one-dimensional ball detector."""

from .compat import array, box_filter, join_1d, np, shape_2d


class ProfileDetector:
    """Detect a ball from a column profile or a grayscale ROI.

    ``u`` is relative to the supplied ROI.  ``update_image`` computes the
    column mean once and delegates to ``update``.
    """

    def __init__(self, background, params, width=None):
        reference = array(background, dtype=float)
        if len(reference.shape) != 1:
            raise ValueError("background must be one-dimensional")
        self.width = int(reference.size)
        if width is not None and int(width) != self.width:
            raise ValueError("explicit width differs from calibration")
        self.diameter = int(params.get("diameter_px", 20))
        self.half = max(1, self.diameter // 2)
        self.gate = float(params.get("gate_px", 20))
        self.snr_min = float(params.get("snr_min", 6))
        self.snr_good = float(params.get("snr_good", 20))
        self.noise_floor = float(params.get("noise_floor", 0.5))
        self.alpha = float(params.get("background_alpha", 0.05))
        self.polarity = params.get("polarity", "both")
        self.noise_update_interval = max(
            1, int(params.get("noise_update_interval", 30))
        )
        self.recovery_frames = max(
            1, int(params.get("background_recovery_frames", 30))
        )
        self.recovery_alpha = float(
            params.get("background_recovery_alpha", self.alpha)
        )
        if (
            self.diameter < 3
            or self.diameter > self.width
            or not 0 < self.snr_min < self.snr_good
            or self.noise_floor <= 0
            or self.gate < 0
            or not 0 <= self.alpha <= 1
            or not 0 <= self.recovery_alpha <= 1
            or self.polarity not in ("both", "bright", "dark")
        ):
            raise ValueError("invalid profile detector parameters")

        self.background = array(reference, dtype=float)
        self._deviation = np.zeros(self.width, dtype=float)
        self._smoothed = np.zeros(self.width, dtype=float)
        self._residual = np.zeros(self.width, dtype=float)
        self._kernel = np.ones(self.diameter, dtype=float) / self.diameter
        self._shape_mask = np.ones(self.width, dtype=float)
        self._noise = float(self.noise_floor)
        self._noise_age = self.noise_update_interval
        self._misses = 0
        self._gain = 1.0
        self._offset = 0.0
        self.found = False
        self.u = 0.0
        self.quality = 0
        self.snr = 0.0
        self.diag = {}

    def _fit_illumination(self, profile):
        """Fit current profile to the calibrated profile."""
        ref_mean = float(np.mean(self.background))
        cur_mean = float(np.mean(profile))
        centered = self.background - ref_mean
        denominator = float(np.sum(centered * centered))
        if denominator <= 1e-9:
            gain = 1.0
        else:
            gain = float(np.sum(centered * (profile - cur_mean)) / denominator)
        gain = min(4.0, max(0.25, gain))
        offset = cur_mean - gain * ref_mean
        self._gain = gain
        self._offset = offset
        self._residual[:] = profile - (gain * self.background + offset)

    def _polarity_values(self):
        if self.polarity == "dark":
            return np.maximum(0.0, -self._residual)
        if self.polarity == "bright":
            return np.maximum(0.0, self._residual)
        return np.abs(self._residual)

    def _set_not_found(self):
        self.found = False
        self.u = 0.0
        self.quality = 0
        self.snr = 0.0

    def update(self, profile, predicted_u=None):
        profile = array(profile, dtype=float)
        if len(profile.shape) != 1 or int(profile.size) != self.width:
            raise ValueError("column profile width differs from calibration")
        self._set_not_found()
        self._fit_illumination(profile)
        self._deviation[:] = self._polarity_values()

        if self._noise_age >= self.noise_update_interval:
            median = float(np.median(self._residual))
            mad = float(np.median(np.abs(self._residual - median)))
            self._noise = max(self.noise_floor, 1.4826 * mad)
            self._noise_age = 0
        self._noise_age += 1

        self._smoothed[:] = box_filter(self._deviation, self.diameter)
        if predicted_u is None:
            peak = int(np.argmax(self._smoothed))
        else:
            prediction = float(predicted_u)
            left = max(0, int(prediction - self.gate))
            right = min(self.width, int(prediction + self.gate) + 1)
            if left >= right:
                peak = int(np.argmax(self._smoothed))
            else:
                peak = left + int(np.argmax(self._smoothed[left:right]))

        score = float(self._smoothed[peak])
        self.snr = score / max(self._noise, self.noise_floor)
        self.diag = {
            "peak": peak,
            "score": score,
            "noise": self._noise,
            "gain": self._gain,
            "offset": self._offset,
            "misses": self._misses,
        }
        if self.snr < self.snr_min:
            self._misses += 1
            if self._misses >= self.recovery_frames:
                target = self._gain * self.background + self._offset
                self.background[:] = (
                    (1.0 - self.recovery_alpha) * self.background
                    + self.recovery_alpha * target
                )
                self._misses = 0
            return self

        peak_half = float(self._deviation[peak]) * 0.5
        left_fwhm = peak
        while left_fwhm > 0 and self._deviation[left_fwhm - 1] >= peak_half:
            left_fwhm -= 1
        right_fwhm = peak
        while (
            right_fwhm + 1 < self.width
            and self._deviation[right_fwhm + 1] >= peak_half
        ):
            right_fwhm += 1
        fwhm = right_fwhm - left_fwhm + 1
        self.diag["fwhm_px"] = fwhm
        if fwhm < 0.6 * self.diameter or fwhm > 1.5 * self.diameter:
            self._misses += 1
            return self

        left = max(0, peak - self.half)
        right = min(self.width, peak + self.half + 1)
        floor = min(float(self._deviation[left]), float(self._deviation[right - 1]))
        weights = np.maximum(0.0, self._deviation[left:right] - floor)
        mass = float(np.sum(weights))
        if mass <= 0:
            self._misses += 1
            return self
        positions = np.arange(left, right, dtype=float)
        self.u = float(np.sum(positions * weights) / mass)
        self.quality = int(
            min(
                255.0,
                max(
                    0.0,
                    255.0 * (self.snr - self.snr_min)
                    / (self.snr_good - self.snr_min),
                ),
            )
        )
        self.found = True
        self._misses = 0

        self._shape_mask[:] = 1.0
        self._shape_mask[
            max(0, int(self.u - 1.5 * self.diameter)):
            min(self.width, int(self.u + 1.5 * self.diameter) + 1)
        ] = 0.0
        adapted = self.background + self.alpha * (
            profile - self.background
        )
        self.background[:] = self.background + self._shape_mask * (
            adapted - self.background
        )
        return self

    def update_image(self, gray_roi, predicted_u=None):
        image = array(gray_roi, dtype=float)
        rows, columns = shape_2d(image)
        if rows == 0 or columns != self.width:
            raise ValueError("image width differs from calibration")
        result = self.update(np.mean(image, axis=0), predicted_u=predicted_u)
        if not result.found:
            return result
        column = image[:, int(round(result.u))]
        edge = min(5, max(1, int(column.size // 5)))
        baseline = float(np.median(join_1d(column[:edge], column[-edge:])))
        if self.polarity == "dark":
            vertical = np.maximum(0.0, baseline - column)
        elif self.polarity == "bright":
            vertical = np.maximum(0.0, column - baseline)
        else:
            vertical = np.abs(column - baseline)
        vertical_peak = float(np.max(vertical))
        vertical_above = np.where(vertical >= vertical_peak * 0.5)[0]
        vertical_width = (
            int(vertical_above[-1] - vertical_above[0] + 1)
            if vertical_above.size else 0
        )
        result.diag["vertical_fwhm_px"] = vertical_width
        if vertical_width > 1.5 * self.diameter:
            result.found = False
            result.quality = 0
            result.snr = 0.0
        return result

    def detect(self, roi_img_or_array, ctx=None):
        ctx = {} if ctx is None else ctx
        predicted = ctx.get("predicted_u")
        values = array(roi_img_or_array)
        if len(values.shape) == 2:
            self.update_image(values, predicted_u=predicted)
        else:
            self.update(values, predicted_u=predicted)
        return self.result()

    def result(self):
        return (
            bool(self.found),
            float(self.u),
            int(self.quality),
            dict(self.diag),
        )
