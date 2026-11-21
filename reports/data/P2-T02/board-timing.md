# P2 板上耗时复测

- 脚本：`tools/board/p2_timing.py`。
- 每项 1,000 次。
- 输入是板上 ulab 数组，避免把主机/脚本构造 Python list 的时间混入测量。
- `gc.mem_free` 前后差值作为分配压力指标，不能等同于精确的分配次数。

| 操作 | p50 µs | p95 µs | p99 µs | max µs |
|---|---:|---:|---:|---:|
| ProfileDetector.update | 2,186 | 2,372 | 78,443 | 78,768 |
| MarkerDetector.update_image | 364 | 528 | 562 | 70,758 |
| geometry FULL | 226 | 396 | 424 | 2,871 |
| geometry two-point | 283 | 463 | 487 | 2,918 |

Profile 和 marker 的 p99/max 离群来自运行时回收或系统调度，p95 仍在目标内。逐项原始输出见 `board-timing.log`。
