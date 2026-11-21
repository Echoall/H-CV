try:
    import numpy as np
except ImportError:
    import ulab
    np = ulab.numpy


def as_array(value, dtype=None):
    return np.array(value, dtype=dtype) if dtype is not None else np.array(value)


def array(value, dtype=None):
    if dtype is None:
        return np.array(value)
    if hasattr(value, "dtype") and dtype is float:
        native_float = getattr(np, "float", None)
        if native_float is not None and value.dtype == native_float:
            return value
        if str(value.dtype).startswith("float"):
            return value
    try:
        return np.array(value, dtype=dtype)
    except TypeError:
        return np.array(value)


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


def zeros(size, dtype=None):
    try:
        return np.zeros(size, dtype=dtype)
    except TypeError:
        return np.zeros(size)


def ones(size, dtype=None):
    try:
        return np.ones(size, dtype=dtype)
    except TypeError:
        return np.ones(size)


def arange(start, stop=None, dtype=None):
    try:
        if stop is None:
            return np.arange(start, dtype=dtype)
        return np.arange(start, stop, dtype=dtype)
    except TypeError:
        if stop is None:
            return np.arange(start)
        return np.arange(start, stop)


def absolute(value):
    try:
        return np.fabs(value)
    except AttributeError:
        return abs(value)
