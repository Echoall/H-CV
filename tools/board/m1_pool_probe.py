import gc
import sys
import time
sys.path.insert(0, "/sdcard/hcv")
from hcv import guard
import image
import ulab.numpy as np

guard.start(5)
width = 640
rows = 48
source = image.Image(width, 64, image.GRAYSCALE)
view = source.to_numpy_ref()
for label in ("gradient", "nonlinear", "uint8_max"):
    view[:, :] = 251
    for row in range(rows):
        guard.tick()
        value = row * 5 if label == "gradient" else (255 if label == "uint8_max" else (240 if row < 12 else 0))
        view[row + 8, :width // 2] = value
        view[row + 8, width // 2:] = 255 - value
    original = bytes(view.tobytes())
    for method in ("pool", "scale_area", "mean_bytes"):
        guard.tick()
        gc.collect()
        before = gc.mem_free()
        started = time.ticks_us()
        try:
            if method == "mean_bytes":
                result = np.array(np.mean(view[8:56, :], axis=0), dtype=np.uint8)
            else:
                cropped = source.crop(roi=(0, 8, width, rows))
                if method == "pool":
                    reduced = cropped.mean_pool(1, rows)
                else:
                    reduced = cropped.scale(1.0, 1.0 / rows, hint=image.AREA)
                result = reduced.to_numpy_ref()[0, :]
        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
            print("REDUCE_ERROR,method=%s,case=%s,error=%r" % (method, label, exc))
            continue
        elapsed = time.ticks_diff(time.ticks_us(), started)
        allocated = before - gc.mem_free()
        left = result[:width // 2]
        right = result[width // 2:]
        print("REDUCE_PROBE,method=%s,case=%s,shape=%s,left_min=%s,left_max=%s,right_min=%s,right_max=%s,us=%s,gc_alloc=%s,source_unchanged=%s" % (method, label, result.shape, np.min(left), np.max(left), np.min(right), np.max(right), elapsed, allocated, original == bytes(view.tobytes())))
