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
for row in range(rows):
    guard.tick()
    view[row + 8, :] = row * 5

def mean_bytes(roi):
    return np.array(np.mean(roi, axis=0), dtype=np.uint8)

def widened_sum(roi):
    return np.sum(np.array(roi, dtype=np.uint16), axis=0)

def sum_dtype(roi):
    return np.sum(roi, axis=0, dtype=np.uint16)

for method in (mean_bytes, widened_sum, sum_dtype):
    times = []
    allocations = []
    for _ in range(10):
        guard.tick()
        gc.collect()
        before = gc.mem_free()
        now = time.ticks_us()
        try:
            result = method(view[8:56, :])
        except TypeError as error:
            print("REDUCE_UNSUPPORTED,method=%s,error=%r" % (method.__name__, error))
            break
        elapsed = time.ticks_diff(time.ticks_us(), now)
        allocated = before - gc.mem_free()
        times.append(elapsed)
        allocations.append(allocated)
    else:
        print("REDUCE_COST,method=%s,runs=%d,us=%s,gc_alloc=%s,dtype=%s,min=%s,max=%s" % (method.__name__, len(times), times, allocations, result.dtype, np.min(result), np.max(result)))
print("IMAGE_REDUCE_API,mean_pool=%s,area=%s" % (hasattr(source, "mean_pool"), getattr(image, "AREA", None)))
