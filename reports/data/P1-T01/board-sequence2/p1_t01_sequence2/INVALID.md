# INVALID

这组 6 帧来自早期实现。`ulab.ndarray.tobytes()` 与 CanMV 帧缓冲共享内存，导致 6 帧像素哈希完全相同。它们不能用于评测或后续实验。

修复后的有效数据在 `reports/data/P1-T01/board-full3/` 和 `reports/data/P1-T01/board-roi3/`。
