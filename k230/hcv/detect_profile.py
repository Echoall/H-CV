"""Vectorised one-dimensional ball detector."""

from .compat import arange, array, box_filter, ones, np, shape_2d, zeros


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
        self._deviation = zeros(self.width, dtype=float)
        self._smoothed = zeros(self.width, dtype=float)
        self._residual = zeros(self.width, dtype=float)
        self._kernel = ones(self.diameter, dtype=float) / self.diameter
        self._shape_mask = ones(self.width, dtype=float)
        self._shape_image = None
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
        gain = 1.0
        offset = float(np.mean(profile) - np.mean(self.background))
        for _ in range(2):
            residual = profile - (gain * self.background + offset)
            center = float(np.median(residual))
            spread = max(
                1.0,
                1.4826 * float(np.median(np.abs(residual - center))),
            )
            weights = (np.abs(residual - center) <= 3.0 * spread) * 1.0
            weight_sum = float(np.sum(weights))
            ref_mean = float(np.sum(weights * self.background) / weight_sum)
            cur_mean = float(np.sum(weights * profile) / weight_sum)
            centered = self.background - ref_mean
            denominator = float(np.sum(weights * centered * centered))
            if denominator <= 1e-9:
                gain = 1.0
            else:
                gain = float(
                    np.sum(
                        weights * centered * (profile - cur_mean)
                    ) / denominator
                )
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

    def _vertical_width(self, image, center_column):
        rows, columns = shape_2d(image)
        column = image[:, center_column]
        neighbor_left = image[
            :, max(0, center_column - 5 * self.diameter):
            max(0, center_column - 4 * self.diameter)
        ]
        neighbor_right = image[
            :, min(columns, center_column + 4 * self.diameter):
            min(columns, center_column + 5 * self.diameter)
        ]
        if neighbor_left.shape[1] and neighbor_right.shape[1]:
            baseline_rows = (
                np.mean(neighbor_left, axis=1)
                + np.mean(neighbor_right, axis=1)
            ) * 0.5
        elif neighbor_left.shape[1]:
            baseline_rows = np.mean(neighbor_left, axis=1)
        elif neighbor_right.shape[1]:
            baseline_rows = np.mean(neighbor_right, axis=1)
        else:
            baseline_rows = ones(rows) * float(np.mean(column))
        if self.polarity == "dark":
            vertical = np.maximum(0.0, baseline_rows - column)
        elif self.polarity == "bright":
            vertical = np.maximum(0.0, column - baseline_rows)
        else:
            vertical = np.abs(column - baseline_rows)
        peak = float(np.max(vertical))
        above = np.where(vertical >= peak * 0.5)[0]
        return int(above[-1] - above[0] + 1) if above.size else 0

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
        local = np.where(
            (self._smoothed[1:-1] >= self._smoothed[:-2])
            & (self._smoothed[1:-1] >= self._smoothed[2:])
        )[0] + 1
        candidates = [int(value) for value in local]
        if not candidates:
            candidates = [int(np.argmax(self._smoothed))]
        if predicted_u is not None:
            prediction = float(predicted_u)
            gated = [
                value for value in candidates
                if abs(value - prediction) <= self.gate
            ]
            if gated:
                candidates = gated
        candidates.sort(
            key=lambda value: float(self._smoothed[value]), reverse=True
        )
        peak = candidates[0]
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
        valid_peak = None
        valid_fwhm = 0
        for candidate in candidates:
            candidate_score = float(self._smoothed[candidate])
            candidate_snr = candidate_score / max(self._noise, self.noise_floor)
            if candidate_snr < self.snr_min:
                continue
            candidate_half = float(self._deviation[candidate]) * 0.5
            candidate_left = candidate
            while (
                candidate_left > 0
                and self._deviation[candidate_left - 1] >= candidate_half
            ):
                candidate_left -= 1
            candidate_right = candidate
            while (
                candidate_right + 1 < self.width
                and self._deviation[candidate_right + 1] >= candidate_half
            ):
                candidate_right += 1
            candidate_fwhm = candidate_right - candidate_left + 1
            if 0.6 * self.diameter <= candidate_fwhm <= 1.5 * self.diameter:
                if self._shape_image is not None:
                    vertical_fwhm = self._vertical_width(
                        self._shape_image, candidate
                    )
                    if vertical_fwhm > 1.5 * self.diameter:
                        continue
                valid_peak = candidate
                valid_fwhm = candidate_fwhm
                self.snr = candidate_snr
                if self._shape_image is not None:
                    self.diag["vertical_fwhm_px"] = vertical_fwhm
                break
        if valid_peak is None:
            self._misses += 1
            if self._misses >= self.recovery_frames:
                target = self._gain * self.background + self._offset
                self.background[:] = (
                    (1.0 - self.recovery_alpha) * self.background
                    + self.recovery_alpha * target
                )
                self._misses = 0
            return self
        peak = valid_peak
        self.diag["fwhm_px"] = valid_fwhm

        left = max(0, peak - self.half)
        right = min(self.width, peak + self.half + 1)
        floor = min(float(self._deviation[left]), float(self._deviation[right - 1]))
        weights = np.maximum(0.0, self._deviation[left:right] - floor)
        mass = float(np.sum(weights))
        if mass <= 0:
            self._misses += 1
            return self
        positions = arange(left, right, dtype=float)
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
        self._shape_image = image
        result = self.update(np.mean(image, axis=0), predicted_u=predicted_u)
        self._shape_image = None
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
