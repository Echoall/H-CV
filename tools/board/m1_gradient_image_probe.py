import gc
import sys
sys.path.insert(0, "/sdcard/hcv")
from hcv import guard
import image
import ulab.numpy as np

guard.start(5)
width = 640
rows = 48
for label in ("gradient", "nonlinear"):
    guard.tick()
    source = image.Image(width, rows + 16, image.GRAYSCALE)
    view = source.to_numpy_ref()
    view[:, :] = 251
    for row in range(rows):
        guard.tick()
        value = row * 5 if label == "gradient" else (240 if row < 12 else 0)
        view[row + 8, :] = value
    original = bytes(view.tobytes())
    gc.collect()
    before = gc.mem_free()
    crop = source.crop(roi=(0, 8, width, rows))
    crop_alloc = before - gc.mem_free()
    result = crop.scale(1.0, 1.0 / rows).to_numpy_ref()
    expected = 117.5 if label == "gradient" else 60
    print("SCALE_PROBE,case=%s,shape=%s,min=%s,max=%s,expected=%s" % (label, result.shape, np.min(result), np.max(result), expected))
    print("CROP_PROBE,case=%s,shape=%s,gc_alloc=%s,source_bytes_unchanged=%s,same_address=%s" % (label, crop.to_numpy_ref().shape, crop_alloc, original == bytes(view.tobytes()), crop.virtaddr() == source.virtaddr()))
