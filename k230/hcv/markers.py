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
            profile = np.mean(image[y0:y1, x0:x1], axis=0)
            edge = min(3, max(1, int(profile.size // 4)))
            baseline = float(np.median(join_1d(profile[:edge], profile[-edge:])))
            if self.polarity == "bright":
                depth = np.maximum(0.0, profile - baseline)
            else:
                depth = np.maximum(0.0, baseline - profile)
            peak = int(np.argmax(depth))
            peak_depth = float(depth[peak])
            if peak_depth < self.min_depth:
                continue
            half = peak_depth * 0.5
            selected = np.where(depth >= half)[0]
            if selected.size == 0:
                continue
            width_px = float(selected[-1] - selected[0] + 1)
            if not 0.5 * self.marker_width <= width_px <= 1.5 * self.marker_width:
                continue
            weights = depth[selected]
            total = float(np.sum(weights))
            position = x0 + float(np.sum(selected * weights) / total)
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
