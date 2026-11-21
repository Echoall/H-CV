try:
    import numpy as np
except ImportError:
    import ulab
    np = ulab.numpy


def as_array(value, dtype=None):
    return np.array(value, dtype=dtype) if dtype is not None else np.array(value)


def array(value, dtype=None):
    return np.array(value, dtype=dtype) if dtype is not None else np.array(value)


def box_filter(values, width):
    """Return numpy ``same`` convolution using ulab's full convolution."""
    count = int(len(values))
    kernel_width = max(1, int(width))
    kernel = np.ones(kernel_width) / kernel_width
    full = np.convolve(values, kernel)
    start = (kernel_width - 1) // 2
    return full[start:start + count]


def shape_2d(value):
    rows = len(value)
    columns = len(value[0]) if rows else 0
    return rows, columns


def join_1d(left, right):
    return np.array(list(left) + list(right))
