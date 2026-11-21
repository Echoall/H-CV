"""Reference-marker detector using narrow column-profile searches."""

from .compat import array, join_1d, shape_2d, np


class MarkerDetector:
    def __init__(self, reference_u, params=None):
        params = {} if params is None else params
        self.reference_u = [float(value) for value in reference_u]
        self.search = int(params.get("search_px", 12))
        self.capture_search = int(
            params.get("capture_search_px", max(60, self.search * 5))
        )
        self.marker_width = float(params.get("marker_width_px", 6))
        self.min_depth = float(params.get("marker_min_depth", 8))
        self.polarity = params.get("polarity", "dark")
        self.marker_v = params.get("marker_v_px")
        self.marker_height = float(params.get("marker_height_px", 12))
        if len(self.reference_u) != 3:
            raise ValueError("three reference markers are required")
        if self.search < 1 or self.marker_width <= 0:
            raise ValueError("invalid marker parameters")
        self.positions = [None, None, None]
        self.valid = [False, False, False]
        self.depths = [0.0, 0.0, 0.0]

    def update_image(self, gray_image, predicted_u=None):
        image = array(gray_image, dtype=float)
        height, width = shape_2d(image)
        if height == 0 or width == 0:
            raise ValueError("gray_image must be two-dimensional")
        marker_v = height * 0.42 if self.marker_v is None else float(self.marker_v)
        y0 = max(0, int(marker_v - self.marker_height))
        y1 = min(height, int(marker_v + self.marker_height + 1))
        previous_positions = list(self.positions)
        previous_valid = list(self.valid)
        self.positions = [None, None, None]
        self.valid = [False, False, False]
        self.depths = [0.0, 0.0, 0.0]
        predictions = self.reference_u if predicted_u is None else predicted_u
        for index, reference in enumerate(predictions):
            if reference is None:
                continue
            if predicted_u is None and previous_valid[index]:
                center = int(round(float(previous_positions[index])))
                window = self.search
            else:
                center = int(round(float(reference)))
                window = self.search if predicted_u is not None else self.capture_search
            x0 = max(0, center - window)
            x1 = min(width, center + window + 1)
            if x1 - x0 < 3:
                continue
            try:
                profile = np.mean(
                    image[y0:y1, x0:x1],
                    axis=0,
                )
                baseline = float(np.median(profile))
            except (AttributeError, TypeError, ValueError):
                profile = [
                    sum(
                        float(image[row, column])
                        for row in range(y0, y1)
                    ) / float(y1 - y0)
                    for column in range(x0, x1)
                ]
                ordered = sorted(profile)
                middle = len(ordered) // 2
                if len(ordered) % 2:
                    baseline = float(ordered[middle])
                else:
                    baseline = float(
                        (ordered[middle - 1] + ordered[middle]) * 0.5
                    )
            if self.polarity == "bright":
                depth = np.maximum(0.0, profile - baseline)
            else:
                depth = np.maximum(0.0, baseline - profile)
            peak = int(np.argmax(depth))
            peak_depth = float(depth[peak])
            if peak_depth < self.min_depth:
                continue
            half = peak_depth * 0.5
            if window == self.search:
                start = peak
                while start > 0 and float(depth[start - 1]) >= half:
                    start -= 1
                end = peak
                while end + 1 < len(depth) and float(depth[end + 1]) >= half:
                    end += 1
            else:
                runs = []
                run_start = None
                for profile_index in range(len(depth)):
                    if float(depth[profile_index]) >= half:
                        if run_start is None:
                            run_start = profile_index
                    elif run_start is not None:
                        runs.append((run_start, profile_index - 1))
                        run_start = None
                if run_start is not None:
                    runs.append((run_start, len(depth) - 1))
                candidates = []
                for run_start, run_end in runs:
                    width_px = float(run_end - run_start + 1)
                    if not 0.5 * self.marker_width <= width_px <= 1.5 * self.marker_width:
                        continue
                    run_peak = int(np.argmax(depth[run_start:run_end + 1]))
                    run_peak += run_start
                    candidates.append(
                        (
                            -float(depth[run_peak]),
                            abs(float(x0 + run_peak) - float(center)),
                            run_start,
                            run_end,
                            run_peak,
                        )
                    )
                if not candidates:
                    continue
                _, _, start, end, peak = min(candidates)
            peak_depth = float(depth[peak])
            total = 0.0
            weighted = 0.0
            for profile_index in range(start, end + 1):
                weight = float(depth[profile_index])
                total += weight
                weighted += profile_index * weight
            if total <= 0.0:
                continue
            position = x0 + weighted / total
            self.positions[index] = position
            self.valid[index] = True
            self.depths[index] = peak_depth
        return self

    def update(self, gray_image, predicted_u=None):
        return self.update_image(gray_image, predicted_u=predicted_u)

    def detect(self, gray_image, ctx=None):
        ctx = {} if ctx is None else ctx
        self.update_image(gray_image, predicted_u=ctx.get("predicted_u"))
        return self.result()

    def result(self):
        return {
            "positions": list(self.positions),
            "valid": list(self.valid),
            "depths": list(self.depths),
            "count": sum(self.valid),
        }
