# M3 bind-order probe evidence

时间：2026-11-22 00:14（香港时间）。板卡先经 `ports`、`info`，并已按批准读取官方示例。每个入口 5 秒 guard，运行前后没有 WDT、reset 或根 `main.py` 操作。

- `CHN1` 配置：通过。`chn1_configured`，随后 `sensor_stop`、`sensor_deinit`，退出码 0，`info` 通过。
- `SetOutBufs`：通过。`set_out_bufs`，随后完整清理，退出码 0，`info` 通过。
- `Create-before-init`：调用 `kd_mpi_venc_create_chn` 返回，随后完整清理，退出码 0，`info` 通过。这个顺序只作为诊断探针，不代表已批准的工作顺序。
- `link-init-create`：`CHN1` 配置、`SetOutBufs`、`bind_info(chn=1)` 都返回，卡在下一步 `MediaManager.link`。主机受保护恢复最终报告 raw REPL recovery failed，退出码 3。没有继续运行其它顺序，避免把失联后的状态当作新证据。

原始输出在本目录。卡点已从整体 `configure_and_bind_chn1` 缩小到 CHN1 → VENC 的 `MediaManager.link` 调用。由于底层调用未返回，不能据此判断链接参数、CHN1 支持矩阵还是媒体状态是哪一个原因。
