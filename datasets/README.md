# 数据集 v1 格式

目录结构：

```text
datasets/<dataset_name>/<scene>/
  meta.json
  frames/
    frame_000000.bmp
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

原始图像使用无损 BMP，序列数据优先使用原始 `.bin` 加每帧 JSON。提交到仓库的样本保持小。大文件使用 Git LFS，或只在 `meta.json` 和报告中记录外部位置、大小和 SHA-256。不要提交 WiFi 密码或其他本地配置。

一个示例数据集至少包含 3 个场景目录，每个目录都必须有 `meta.json` 和可复现的原始文件清单。
