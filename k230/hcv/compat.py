try:
    import numpy as np
except ImportError:
    import ulab.numpy as np


def as_array(value, dtype=None):
    return np.array(value, dtype=dtype) if dtype is not None else np.array(value)
