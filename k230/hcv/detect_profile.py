"""Column-profile detection against a calibrated empty-slot reference."""


class ProfileDetector:
    """Reuse work buffers across calls. Column means are supplied by the caller."""

    def __init__(self, background, params):
        self.width = len(background)
        self.diameter = params["diameter_px"]
        self.half = self.diameter // 2
        self.gate = params["gate_px"]
        self.snr_min = params["snr_min"]
        self.snr_good = params["snr_good"]
        self.noise_floor = params["noise_floor"]
        self.alpha = params["background_alpha"]
        self.polarity = params["polarity"]
        if (self.diameter < 3 or self.diameter > self.width
                or not 0 < self.snr_min < self.snr_good or self.noise_floor <= 0
                or self.gate < 0 or not 0 <= self.alpha <= 1
                or self.polarity not in ("both", "bright", "dark")):
            raise ValueError("invalid profile detector parameters")
        self.background = list(background)
        self.deviation = [0.0] * self.width
        self.smoothed = [0.0] * self.width
        self.sorted_values = [0.0] * self.width
        self.found = False
        self.u = 0.0
        self.quality = 0
        self.snr = 0.0

    def _median(self):
        self.sorted_values.sort()
        middle = self.width // 2
        if self.width % 2:
            return self.sorted_values[middle]
        return (self.sorted_values[middle - 1] + self.sorted_values[middle]) * 0.5

    def update(self, profile, predicted_u=None):
        if len(profile) != self.width:
            raise ValueError("column profile width differs from calibration")
        self.found = False
        self.u = 0.0
        self.quality = 0
        self.snr = 0.0
        for column in range(self.width):
            delta = float(profile[column]) - self.background[column]
            if self.polarity == "both":
                delta = abs(delta)
            elif self.polarity == "dark":
                delta = -delta
            value = max(0.0, delta)
            self.deviation[column] = value
            self.sorted_values[column] = value
        median = self._median()
        for column in range(self.width):
            self.sorted_values[column] = abs(self.deviation[column] - median)
        noise = max(self.noise_floor, 1.4826 * self._median())

        left = 0
        right = 0
        window_sum = 0.0
        peak = 0
        gated_peak = -1
        for column in range(self.width):
            target_right = min(self.width, column - self.half + self.diameter)
            while right < target_right:
                window_sum += self.deviation[right]
                right += 1
            if left < column - self.half:
                window_sum -= self.deviation[left]
                left += 1
            score = window_sum / (right - left)
            self.smoothed[column] = score
            if score > self.smoothed[peak]:
                peak = column
            if (predicted_u is not None and abs(column - predicted_u) <= self.gate
                    and (gated_peak == -1 or score > self.smoothed[gated_peak])):
                gated_peak = column
        if gated_peak >= 0 and self.smoothed[gated_peak] / noise >= self.snr_min:
            peak = gated_peak

        self.snr = self.smoothed[peak] / noise
        if self.snr < self.snr_min:
            return
        left = max(0, peak - self.half)
        right = min(self.width - 1, peak + self.half)
        floor = min(self.deviation[left], self.deviation[right])
        mass = 0.0
        moment = 0.0
        for column in range(left, right + 1):
            weight = max(0.0, self.deviation[column] - floor)
            mass += weight
            moment += column * weight
        self.u = moment / mass if mass else float(peak)
        self.quality = int(min(255.0, 255.0 * (self.snr - self.snr_min) /
                               (self.snr_good - self.snr_min)))
        self.found = True
        for column in range(self.width):
            if abs(column - self.u) > 1.5 * self.diameter:
                self.background[column] += self.alpha * (
                    float(profile[column]) - self.background[column]
                )
