"""Vectorised one-dimensional ball detector."""

import gc

from .compat import absolute, arange, array, box_filter, ones, np, shape_2d, zeros


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
        self.fast = bool(params.get("m1_fast", False))
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
        self._fast_noise = self.noise_floor
        self._fast_frame = 0
        self._fast_retrying = False
        self._fast_retry_count = 0
        self._fast_exclude_left = -1
        self._fast_exclude_right = -1
        self._fast_background = None
        self._fast_background_sum = 0
        self._fast_background_sq_sum = 0
        self._fast_probe = None
        self._fast_threshold = 0
        self.found = False
        self.u = 0.0
        self.quality = 0
        self.snr = 0.0
        self.diag = {}
        if self.fast:
            fast_background = bytearray(self.width)
            for index in range(self.width):
                value = int(reference[index])
                fast_background[index] = max(0, min(255, value))
                self._fast_background_sum += fast_background[index]
                self._fast_background_sq_sum += (
                    fast_background[index] * fast_background[index]
                )
            self._fast_background = fast_background
            self._fast_threshold = max(
                1,
                int(self._fast_noise * self.snr_min),
            )
            self.diag = {
                "peak": 0,
                "score": 0,
                "noise": self._fast_noise,
                "fwhm_px": 0,
            }

    def _fit_illumination(self, profile):
        """Fit current profile to the calibrated profile."""
        gain = 1.0
        offset = float(np.mean(profile) - np.mean(self.background))
        for _ in range(2):
            residual = profile - (gain * self.background + offset)
            center = float(np.median(residual))
            spread = max(
                1.0,
                1.4826 * float(np.median(absolute(residual - center))),
            )
            weights = (absolute(residual - center) <= 3.0 * spread) * 1.0
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
        return absolute(self._residual)

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
            vertical = absolute(column - baseline_rows)
        peak = float(np.max(vertical))
        above_values = np.where(
            vertical >= peak * 0.5,
            arange(rows),
            -1,
        )
        above = [int(value) for value in above_values if int(value) >= 0]
        return int(above[-1] - above[0] + 1) if above else 0

    def update(self, profile, predicted_u=None):
        if self.fast:
            return self._update_fast(profile, predicted_u=predicted_u)
        profile = array(profile, dtype=float)
        if len(profile.shape) != 1 or int(profile.size) != self.width:
            raise ValueError("column profile width differs from calibration")
        self._set_not_found()
        self._fit_illumination(profile)
        self._deviation[:] = self._polarity_values()

        if self._noise_age >= self.noise_update_interval:
            median = float(np.median(self._residual))
            mad = float(np.median(absolute(self._residual - median)))
            self._noise = max(self.noise_floor, 1.4826 * mad)
            self._noise_age = 0
        self._noise_age += 1

        self._smoothed[:] = box_filter(self._deviation, self.diameter)
        local_values = np.where(
            (self._smoothed[1:-1] >= self._smoothed[:-2])
            & (self._smoothed[1:-1] >= self._smoothed[2:])
            & (self._smoothed[1:-1] > 0.0),
            arange(1, self.width - 1),
            -1,
        )
        candidates = [
            int(value) for value in local_values if int(value) >= 0
        ]
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
            if candidate_half <= 0.0:
                continue
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

    def _update_fast(self, profile, predicted_u=None, _profile_bytes=None):
        """Low-allocation detector with the P2 baseline checks retained."""
        if not self._fast_retrying:
            self._fast_retry_count = 0
            self._fast_exclude_left = -1
            self._fast_exclude_right = -1
        if _profile_bytes is None:
            if not hasattr(profile, "shape"):
                profile = array(profile)
            if len(profile.shape) != 1 or int(profile.size) != self.width:
                raise ValueError("column profile width differs from calibration")
        self._set_not_found()
        self._fast_frame += 1
        probe = self._fast_probe
        if probe is not None:
            probe[0] = gc.mem_free()
        if _profile_bytes is None:
            if str(getattr(profile, "dtype", "")).startswith("float"):
                profile_bytes = bytearray(
                    int(profile[index]) for index in range(self.width)
                )
            else:
                profile_bytes = profile.tobytes()
        else:
            profile_bytes = _profile_bytes
        if probe is not None:
            probe[1] = gc.mem_free()
        background = self._fast_background
        if probe is not None:
            probe[2] = gc.mem_free()
        if self._shape_image is None:
            return self._update_fast_board(
                profile_bytes,
                predicted_u,
                probe,
            )

        count = self.width
        profile_sum = 0
        cross_sum = 0
        for index in range(count):
            value = profile_bytes[index]
            base = background[index]
            profile_sum += value
            cross_sum += base * value
        denominator = (
            count * self._fast_background_sq_sum
            - self._fast_background_sum * self._fast_background_sum
        )
        if denominator > 0:
            gain = (
                count * cross_sum
                - self._fast_background_sum * profile_sum
            ) / denominator
            gain = min(4.0, max(0.25, gain))
        else:
            gain = 1.0
        offset = (
            profile_sum - gain * self._fast_background_sum
        ) / count

        # Remove the small set of ball/interference pixels before the final
        # affine fit. The calibrated profile remains the fixed reference.
        kept_count = 0
        kept_sum = 0
        kept_background_sum = 0
        kept_background_sq_sum = 0
        kept_cross_sum = 0
        for index in range(count):
            base = background[index]
            value = profile_bytes[index]
            residual = value - (gain * base + offset)
            if abs(residual) <= 8.0:
                kept_count += 1
                kept_sum += value
                kept_background_sum += base
                kept_background_sq_sum += base * base
                kept_cross_sum += base * value
        kept_denominator = (
            kept_count * kept_background_sq_sum
            - kept_background_sum * kept_background_sum
        )
        if kept_count > 0 and kept_denominator > 0:
            gain = (
                kept_count * kept_cross_sum
                - kept_background_sum * kept_sum
            ) / kept_denominator
            gain = min(4.0, max(0.25, gain))
            offset = (
                kept_sum - gain * kept_background_sum
            ) / kept_count
        self._gain = gain
        self._offset = offset
        residual_sum = 0.0
        noise_count = kept_count if kept_count > 0 else count
        for index in range(count):
            residual = profile_bytes[index] - (
                gain * background[index] + offset
            )
            if kept_count == 0 or abs(residual) <= 8.0:
                residual_sum += residual
        residual_mean = residual_sum / noise_count
        deviation_sum = 0.0
        for index in range(count):
            residual = profile_bytes[index] - (
                gain * background[index] + offset
            )
            if kept_count == 0 or abs(residual) <= 8.0:
                deviation_sum += abs(residual - residual_mean)
        self._fast_noise = max(
            self.noise_floor,
            1.4826 * deviation_sum / noise_count,
        )
        self._fast_threshold = self.snr_min * self._fast_noise

        peak = -1
        peak_value = 0.0
        peak_score = 0.0
        if predicted_u is None:
            lower = 0
            upper = self.width
        else:
            lower = max(0, int(float(predicted_u) - self.gate))
            upper = min(self.width, int(float(predicted_u) + self.gate + 1))
        if self._shape_image is None:
            for center in range(lower, upper):
                if (
                    self._fast_exclude_left <= center
                    <= self._fast_exclude_right
                ):
                    continue
                residual = profile_bytes[center] - (
                    gain * background[center] + offset
                )
                if self.polarity == "bright":
                    value = residual
                elif self.polarity == "dark":
                    value = -residual
                else:
                    value = abs(residual)
                if value > peak_value:
                    peak_value = value
                    peak = center
            peak_score = peak_value
        else:
            for center in range(lower, upper):
                if (
                    self._fast_exclude_left <= center
                    <= self._fast_exclude_right
                ):
                    continue
                window_start = max(0, center - self.half)
                window_end = min(self.width, center + self.half + 1)
                window_sum = 0.0
                for index in range(window_start, window_end):
                    residual = profile_bytes[index] - (
                        gain * background[index] + offset
                    )
                    if self.polarity == "bright":
                        value = residual
                    elif self.polarity == "dark":
                        value = -residual
                    else:
                        value = abs(residual)
                    if value > 0.0:
                        window_sum += value
                score = window_sum / (window_end - window_start)
                center_residual = profile_bytes[center] - (
                    gain * background[center] + offset
                )
                if self.polarity == "bright":
                    center_value = center_residual
                elif self.polarity == "dark":
                    center_value = -center_residual
                else:
                    center_value = abs(center_residual)
                if score > peak_score:
                    peak_score = score
                    peak_value = center_value
                    peak = center
        if peak < 0 or peak_score < self._fast_threshold:
            if predicted_u is not None:
                peak = -1
                peak_value = 0
                peak_score = 0.0
                for index in range(self.width):
                    if (
                        self._fast_exclude_left <= index
                        <= self._fast_exclude_right
                    ):
                        continue
                    residual = profile_bytes[index] - (
                        gain * background[index] + offset
                    )
                    if self.polarity == "bright":
                        value = residual
                    elif self.polarity == "dark":
                        value = -residual
                    else:
                        value = abs(residual)
                    if value > peak_value:
                        peak_value = value
                        peak = index
                if peak < 0 or peak_value < self._fast_threshold:
                    peak = -1
            else:
                peak = -1
        if peak < 0:
            self._misses += 1
            if probe is not None:
                value = gc.mem_free()
                for index in range(3, 8):
                    probe[index] = value
            return self
        if probe is not None:
            probe[3] = gc.mem_free()
        half = peak_value * 0.5
        outer = self.diameter
        if peak <= outer or peak + outer >= self.width:
            self._misses += 1
            if probe is not None:
                value = gc.mem_free()
                probe[4] = value
                for index in range(5, 8):
                    probe[index] = value
            return self
        left_outer = profile_bytes[peak - outer] - (
            gain * background[peak - outer] + offset
        )
        right_outer = profile_bytes[peak + outer] - (
            gain * background[peak + outer] + offset
        )
        if self.polarity == "dark":
            left_outer = -left_outer
            right_outer = -right_outer
        elif self.polarity == "both":
            left_outer = abs(left_outer)
            right_outer = abs(right_outer)
        if (
            self._shape_image is None
            and (left_outer >= half or right_outer >= half)
        ):
            self._misses += 1
            if probe is not None:
                value = gc.mem_free()
                probe[4] = value
                for index in range(5, 8):
                    probe[index] = value
            return self

        left = max(0, peak - self.half)
        right = min(self.width, peak + self.half + 1)
        width = right - left
        if probe is not None:
            probe[4] = gc.mem_free()
        total = 0.0
        weighted = 0.0
        floor = peak_value * 0.05
        for index in range(left, right):
            residual = profile_bytes[index] - (
                gain * background[index] + offset
            )
            if self.polarity == "bright":
                value = residual
            elif self.polarity == "dark":
                value = -residual
            else:
                value = abs(residual)
            value -= floor
            if value > 0.0:
                total += value
                weighted += index * value
        if total <= 0.0:
            self._misses += 1
            if probe is not None:
                value = gc.mem_free()
                for index in range(5, 8):
                    probe[index] = value
            return self
        if probe is not None:
            probe[5] = gc.mem_free()
        self.u = weighted / total
        self.snr = peak_value / max(self._fast_noise, self.noise_floor)
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
        if probe is not None:
            probe[6] = gc.mem_free()
        self.found = True
        self._misses = 0
        self.diag["peak"] = peak
        self.diag["score"] = peak_value
        self.diag["noise"] = self._fast_noise
        self.diag["fwhm_px"] = width
        if self._shape_image is not None:
            vertical_fwhm = self._fast_vertical_width(
                self._shape_image, peak
            )
            self.diag["vertical_fwhm_px"] = vertical_fwhm
            if vertical_fwhm > 1.5 * self.diameter:
                if self._fast_retry_count < 3:
                    self._fast_retry_count += 1
                    self._fast_exclude_left = max(
                        0, peak - self.diameter
                    )
                    self._fast_exclude_right = min(
                        self.width - 1, peak + self.diameter
                    )
                    self._fast_retrying = True
                    result = self._update_fast(
                        profile,
                        predicted_u=None,
                        _profile_bytes=profile_bytes,
                    )
                    self._fast_retrying = False
                    self._fast_exclude_left = -1
                    self._fast_exclude_right = -1
                    return result
                self._set_not_found()
                self._misses += 1
        if probe is not None:
            probe[7] = gc.mem_free()
        return self

    def _update_fast_board(self, profile_bytes, predicted_u, probe):
        """Integer baseline path used by the live single-row board loop."""
        background = self._fast_background
        peak = -1
        peak_value = 0
        if predicted_u is None:
            lower = 0
            upper = self.width
        else:
            lower = max(0, int(float(predicted_u) - self.gate))
            upper = min(self.width, int(float(predicted_u) + self.gate + 1))
        for index in range(lower, upper):
            residual = background[index] - profile_bytes[index]
            if self.polarity == "bright":
                value = -residual
            elif self.polarity == "dark":
                value = residual
            else:
                value = abs(residual)
            if value > peak_value:
                peak_value = value
                peak = index
        if peak < 0 or peak_value < self._fast_threshold:
            self._misses += 1
            if probe is not None:
                value = gc.mem_free()
                for index in range(3, 8):
                    probe[index] = value
            return self
        if probe is not None:
            probe[3] = gc.mem_free()
        half = peak_value // 2
        outer = self.diameter
        if peak <= outer or peak + outer >= self.width:
            self._misses += 1
            return self
        left_outer = background[peak - outer] - profile_bytes[peak - outer]
        right_outer = background[peak + outer] - profile_bytes[peak + outer]
        if self.polarity == "bright":
            left_outer = -left_outer
            right_outer = -right_outer
        elif self.polarity == "both":
            left_outer = abs(left_outer)
            right_outer = abs(right_outer)
        if left_outer >= half or right_outer >= half:
            self._misses += 1
            return self
        left = max(0, peak - self.half)
        right = min(self.width, peak + self.half + 1)
        width = right - left
        if probe is not None:
            probe[4] = gc.mem_free()
        total = 0.0
        weighted = 0.0
        floor = peak_value * 0.05
        for index in range(left, right):
            residual = background[index] - profile_bytes[index]
            if self.polarity == "bright":
                value = -residual
            elif self.polarity == "both" and residual < 0:
                value = -residual
            else:
                value = residual
            value -= floor
            if value > 0.0:
                total += value
                weighted += index * value
        if total <= 0.0:
            self._misses += 1
            return self
        if probe is not None:
            probe[5] = gc.mem_free()
        self.u = weighted / total
        self.snr = peak_value / max(self._fast_noise, self.noise_floor)
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
        if probe is not None:
            probe[6] = gc.mem_free()
        self.found = True
        self._misses = 0
        self.diag["peak"] = peak
        self.diag["score"] = peak_value
        self.diag["noise"] = self._fast_noise
        self.diag["fwhm_px"] = width
        if probe is not None:
            probe[7] = gc.mem_free()
        return self

    def _fast_vertical_width(self, image, center_column):
        rows, columns = shape_2d(image)
        left = max(0, center_column - 5 * self.diameter)
        left_end = max(0, center_column - 4 * self.diameter)
        right = min(columns, center_column + 4 * self.diameter)
        right_end = min(columns, center_column + 5 * self.diameter)
        values = []
        peak = 0.0
        for row in range(rows):
            center = float(image[row, center_column])
            total = 0.0
            count = 0
            for column in range(left, left_end):
                total += float(image[row, column])
                count += 1
            for column in range(right, right_end):
                total += float(image[row, column])
                count += 1
            baseline = total / count if count else center
            residual = center - baseline
            if self.polarity == "dark":
                value = -residual
            elif self.polarity == "bright":
                value = residual
            else:
                value = abs(residual)
            value = max(0.0, value)
            values.append(value)
            peak = max(peak, value)
        if peak <= 0.0:
            return 0
        half = peak * 0.5
        above = [row for row, value in enumerate(values) if value >= half]
        return above[-1] - above[0] + 1 if above else 0

    def update_image(self, gray_roi, predicted_u=None):
        image = gray_roi if self.fast else array(gray_roi, dtype=float)
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

