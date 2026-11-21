"""Small-window threshold blob detector.

The detector deliberately requires a predicted horizontal position.  This
keeps the expensive threshold/blob path out of the full-frame main loop.
"""

from .compat import absolute, arange, array, shape_2d, np


class BlobDetector:
    def __init__(self, params):
        self.diameter = float(params.get("diameter_px", 20))
        self.polarity = params.get("polarity", "both")
        self.k_dark = float(params.get("k_dark", 3.0))
        self.k_bright = float(params.get("k_bright", 3.0))
        self.min_contrast = float(params.get("min_contrast", 8.0))
        self.distance_lambda = float(params.get("distance_lambda", 1.0))
        self.max_blobs = int(params.get("max_blobs", 32))
        self.sample_stride = max(1, int(params.get("sample_stride_px", 1)))
        self.axis_v = params.get("axis_v_px")
        self.axis_slope = float(params.get("axis_slope", 0.0))
        self.found = False
        self.u = 0.0
        self.quality = 0
        self.snr = 0.0
        self.diag = {}

    def _reset(self):
        self.found = False
        self.u = 0.0
        self.quality = 0
        self.snr = 0.0
        self.diag = {}

    def update_image(self, gray_roi, predicted_u=None):
        self._reset()
        image = array(gray_roi, dtype=float)
        rows, columns = shape_2d(image)
        if rows == 0 or columns == 0:
            raise ValueError("gray_roi must be a two-dimensional image")
        if predicted_u is None:
            self.diag = {"reason": "prediction_required"}
            return self

        height, width = rows, columns
        center = int(round(float(predicted_u)))
        crop_half = 32
        x0 = max(0, center - crop_half)
        x1 = min(width, center + crop_half)
        if x1 <= x0:
            self.diag = {"reason": "prediction_outside_roi"}
            return self
        crop = image[:, x0:x1]
        if self.sample_stride > 1:
            sample = crop[::self.sample_stride, ::self.sample_stride]
        else:
            sample = crop
        median = float(np.median(sample))
        mad = float(np.median(absolute(sample - median)))
        sigma = max(1.4826 * mad, 0.5)
        dark_limit = median - self.k_dark * sigma
        bright_limit = median + self.k_bright * sigma
        if self.polarity == "dark":
            mask = crop <= dark_limit
        elif self.polarity == "bright":
            mask = crop >= bright_limit
        else:
            mask = (crop <= dark_limit) | (crop >= bright_limit)

        # Suppress the slot edges and off-axis clutter.
        rows = arange(height, dtype=float)
        axis_v = height * 0.5 if self.axis_v is None else float(self.axis_v)
        axis = axis_v + self.axis_slope * (
            arange(x0, x1, dtype=float) - float(predicted_u)
        )
        axis_distance = absolute(rows[:, None] - axis[None, :])
        mask &= axis_distance <= 0.75 * self.diameter
        mask &= absolute(
            arange(x0, x1, dtype=float)[None, :] - float(predicted_u)
        ) <= 0.8 * self.diameter

        point_rows, point_columns = np.where(mask)
        point_count = len(point_rows)
        if point_count == 0:
            self.diag = {"reason": "no_threshold_pixels", "noise": sigma}
            return self
        if point_count > self.max_blobs * max(1, int(self.diameter)):
            self.diag = {
                "reason": "threshold_overload",
                "pixels": int(points.shape[0]),
                "noise": sigma,
            }
            return self

        # A compact weighted centroid is sufficient in the prediction crop.
        yy = point_rows
        xx_local = point_columns
        xx = xx_local + x0
        values = array(
            [crop[int(row), int(column)] for row, column in zip(yy, xx_local)],
            dtype=float,
        )
        if self.polarity == "dark":
            weights = np.maximum(0.0, median - values)
        elif self.polarity == "bright":
            weights = np.maximum(0.0, values - median)
        else:
            weights = absolute(values - median)
        total = float(np.sum(weights))
        if total <= 0:
            self.diag = {"reason": "zero_weight", "noise": sigma}
            return self
        candidate_u = float(np.sum(xx * weights) / total)
        span_x = float(np.max(xx) - np.min(xx) + 1)
        span_y = float(np.max(yy) - np.min(yy) + 1)
        density = float(point_count) / max(1.0, span_x * span_y)
        size_match = min(span_x, span_y) / max(1.0, self.diameter)
        contrast = float(np.max(absolute(values - median)))
        self.snr = contrast / sigma
        self.diag = {
            "noise": sigma,
            "pixels": int(point_count),
            "span_x": span_x,
            "span_y": span_y,
            "density": density,
            "contrast": contrast,
        }
        if (
            span_x < 0.5 * self.diameter
            or span_x > 1.5 * self.diameter
            or span_y < 0.5 * self.diameter
            or span_y > 1.5 * self.diameter
            or density < 0.25
            or contrast < self.min_contrast
        ):
            self.diag["reason"] = "shape_or_contrast"
            return self
        self.u = candidate_u
        score = max(0.0, min(1.0, size_match)) * density
        score -= self.distance_lambda * abs(candidate_u - float(predicted_u)) / max(
            1.0, self.diameter
        )
        self.quality = int(max(0.0, min(255.0, 255.0 * score)))
        self.found = self.quality > 0
        return self

    def detect(self, roi_img_or_array, ctx=None):
        ctx = {} if ctx is None else ctx
        self.update_image(
            roi_img_or_array,
            predicted_u=ctx.get("predicted_u"),
        )
        return self.result()

    def result(self):
        return (
            bool(self.found),
            float(self.u),
            int(self.quality),
            dict(self.diag),
        )

