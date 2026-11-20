# 数据集 v1 格式

目录结构：

```text
datasets/<dataset_name>/<scene>/
  meta.json
  frames/
    frame_000000.gray.bin
    frame_000000.json
  sequence/
    frame_000000.bin
    frame_000000.json
```

每个场景一个 `meta.json`，至少包含：

```json
{
  "dataset": "appearance_v1",
  "scene": "L2_center_static",
  "light_id": "L2",
  "tube_color": "white",
  "truth_x_mm": 0.0,
  "rod_angle_deg": 0.0,
  "sensor": {
    "pixformat": "GRAYSCALE",
    "framesize": "1280x720",
    "fps": 90
  },
  "exposure": {
    "auto": true,
    "gain_db": null,
    "exposure_us": null
  },
  "operator_notes": "",
  "capture_mode": "single",
  "created_at_utc": "",
  "files": []
}
```

每帧的 JSON 记录 `seq`、`t_cap_us`、`status`、`u_px`、`pos_raw`、`pos_filt`、`vel`、`quality`、各阶段耗时和条件标签。静态采集必须填写 `truth_x_mm`，人工读刻度误差按约 ±0.5 mm 记录。

板上序列模式把检测通道的 `ulab` 数组立即复制成 `bytearray`，再统一写盘。开始分配前读取 `gc.mem_free()`，如果 `count × 每帧字节数` 超过可用内存的 60% 就直接拒绝。640×360 灰度按每像素 1 字节估算，全帧数量由实测堆动态决定。使用 `--roi-y` 和 `--roi-height` 时可只缓存槽带，例如 640×64 约 41 KiB 一帧。CanMV v1.4 的 YUV420SP 不能通过 `to_numpy_ref()` 转 bytes，且缓存多帧 `Image.copy()` 会耗尽 fast frame buffer，所以序列模式默认关闭 CHN1。单帧模式用 `--capture-chn1` 保存 CHN1 全画面。

检测通道保存为 `640×360` 灰度 `frame_*.gray.bin`，CHN1 保存为原始 `frame_*_chn1.yuv420sp`，每帧 JSON 记录尺寸、格式和字节数。提交到仓库的样本保持小。大文件使用 Git LFS，或只在 `meta.json` 和报告中记录外部位置、大小和 SHA-256。不要提交 WiFi 密码或其他本地配置。

一个示例数据集至少包含 3 个场景目录，每个目录都必须有 `meta.json` 和可复现的原始文件清单。
