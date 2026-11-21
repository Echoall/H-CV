import sys
sys.path.insert(0, "/sdcard/hcv")
from hcv import guard
import ulab.numpy as np

guard.start(5)
rows = 48
cols = 640
gradient = np.zeros((rows, cols), dtype=np.uint8)
for row in range(rows):
    guard.tick()
    gradient[row, :] = row * 5
full = np.full((rows, cols), 255, dtype=np.uint8)
for label, image, expected in (("gradient", gradient, 5640), ("uint8_max", full, 12240)):
    guard.tick()
    for dtype in (np.uint8, np.uint16):
        source = image if dtype == np.uint8 else np.array(image, dtype=dtype)
        sums = np.sum(source, axis=0)
        print("SUM_PROBE,case=%s,input_dtype=%s,shape=%s,dtype=%s,min=%s,max=%s,expected=%s" % (label, source.dtype, sums.shape, sums.dtype, np.min(sums), np.max(sums), expected))
    means = np.mean(image, axis=0)
    print("MEAN_PROBE,case=%s,shape=%s,dtype=%s,min=%s,max=%s,expected=%s" % (label, means.shape, means.dtype, np.min(means), np.max(means), expected / rows))
