"""One-dimensional constant-velocity Kalman tracker."""


class KalmanTracker:
    def __init__(self, params=None):
        params = {} if params is None else params
        self.q_accel = float(params.get("q_accel", 2000.0))
        self.r_meas = float(params.get("r_meas", 0.3))
        self.gate_mm = float(params.get("gate_mm", 15.0))
        self.coast_max = int(params.get("coast_max", 3))
        self.reacquire_frames = int(params.get("reacquire_frames", 3))
        self.reset()

    def reset(self):
        self.initialized = False
        self.lost = True
        self.x = 0.0
        self.v = 0.0
        self.p00 = 1.0
        self.p01 = 0.0
        self.p11 = 1.0
        self.last_t = None
        self.coast = 0
        self.rejects = []
        self.reject_times = []
        self.reacquire = False

    def _dt(self, timestamp_us):
        timestamp_us = int(timestamp_us) & 0xFFFFFFFF
        if self.last_t is None:
            self.last_t = timestamp_us
            return 1.0 / 90.0
        elapsed = (timestamp_us - self.last_t) & 0xFFFFFFFF
        self.last_t = timestamp_us
        if elapsed == 0:
            return 1.0 / 90.0
        return min(1.0, max(1e-5, elapsed / 1_000_000.0))

    def _predict(self, dt):
        self.x += self.v * dt
        q = self.q_accel
        self.p00 += dt * (dt * self.p11 + self.p01 + self.p01) + q * dt ** 3 / 3.0
        self.p01 += dt * self.p11 + q * dt ** 2 / 2.0
        self.p11 += q * dt

    def _output(self, valid, predicted, raw=None):
        return {
            "valid": bool(valid),
            "predicted": bool(predicted),
            "lost": bool(self.lost),
            "reacquire": bool(self.reacquire),
            "position_mm": float(self.x),
            "velocity_mm_s": float(self.v),
            "pos_raw_mm": None if raw is None else float(raw),
            "coast": int(self.coast),
        }

    def step(self, position_mm=None, t_cap_us=0, quality=255):
        self.reacquire = False
        dt = self._dt(t_cap_us)
        if self.initialized:
            self._predict(dt)
        if position_mm is None:
            if not self.initialized:
                return self._output(False, False)
            self.coast += 1
            self.lost = self.coast > self.coast_max
            return self._output(not self.lost, True)

        measurement = float(position_mm)
        if not self.initialized:
            self.initialized = True
            self.lost = False
            self.x = measurement
            self.v = 0.0
            self.p00 = max(0.01, self.r_meas ** 2)
            self.p01 = 0.0
            self.p11 = 100.0
            self.coast = 0
            self.rejects = []
            self.reject_times = []
            return self._output(True, False, measurement)

        quality_norm = max(float(quality) / 255.0, 0.2)
        measurement_noise = (self.r_meas / quality_norm) ** 2
        innovation = measurement - self.x
        innovation_variance = self.p00 + measurement_noise
        gate = max(
            self.gate_mm,
            3.0 * innovation_variance ** 0.5,
            abs(self.v) * dt + self.gate_mm,
        )
        if abs(innovation) <= gate:
            gain_x = self.p00 / innovation_variance
            gain_v = self.p01 / innovation_variance
            old_p01 = self.p01
            self.x += gain_x * innovation
            self.v += gain_v * innovation
            self.p00 = max(1e-9, (1.0 - gain_x) * self.p00)
            self.p01 = (1.0 - gain_x) * self.p01
            self.p11 = max(1e-9, self.p11 - gain_v * old_p01)
            self.coast = 0
            self.lost = False
            self.rejects = []
            self.reject_times = []
            return self._output(True, False, measurement)

        self.rejects.append(measurement)
        self.reject_times.append(int(t_cap_us) & 0xFFFFFFFF)
        if len(self.rejects) > self.reacquire_frames:
            self.rejects.pop(0)
            self.reject_times.pop(0)
        if (
            len(self.rejects) >= self.reacquire_frames
            and max(self.rejects) - min(self.rejects) <= self.gate_mm
        ):
            self.x = measurement
            if len(self.rejects) < 2:
                self.v = 0.0
            else:
                times = [
                    (value - self.reject_times[0]) & 0xFFFFFFFF
                    for value in self.reject_times
                ]
                mean_t = sum(times) / float(len(times))
                mean_x = sum(self.rejects) / float(len(self.rejects))
                denominator = sum(
                    (value - mean_t) ** 2 for value in times
                )
                self.v = (
                    sum(
                        (time_value - mean_t) * (value - mean_x)
                        for time_value, value in zip(times, self.rejects)
                    ) / denominator
                    if denominator > 0 else 0.0
                ) * 1_000_000.0
            self.p00 = max(0.01, self.r_meas ** 2)
            self.p01 = 0.0
            self.p11 = 100.0
            self.coast = 0
            self.lost = False
            self.reacquire = True
            self.rejects = []
            self.reject_times = []
            return self._output(True, False, measurement)

        self.coast += 1
        self.lost = self.coast > self.coast_max
        return self._output(not self.lost, True, measurement)
