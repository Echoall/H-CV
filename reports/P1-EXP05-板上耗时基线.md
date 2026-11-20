# P1-EXP05 板上处理耗时基线

状态：`DOING`，实机原始数据和分析已完成，等待 Claude 验收。

## 实验条件

- K230：幻尔 CanMV K230，COM3，固件 `v1.4-a7de1c8`。
- Sensor：1280×720@90，CHN0 640×360 灰度。
- ROI：`x=0, y=148, w=640, h=48`。
- 每项 1,000 次。
- UART：只使用 UART1，115200，物理 TX1 为 8 脚，未接外部设备。
- 原始日志：`reports/data/P1-EXP05/raw-timing.log`。
- 分析命令：

```text
python tools/analysis/p1_exp05.py reports/data/P1-EXP05/raw-timing.log \
  --output reports/data/P1-EXP05/metrics.json
```

## 结果

单位为微秒，分位数使用 NumPy 默认线性插值。

| 操作 | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| snapshot | 10774 | 11088 | 11558 | 12874 |
| copy ROI | 858 | 2544 | 2723 | 2769 |
| to_numpy_ref + mean | 410 | 540 | 2149 | 2331 |
| sum + box filter + argmax | 477 | 2091 | 2310 | 78961 |
| find_blobs 暗阈值 | 12173 | 13662 | 13796 | 15107 |
| find_blobs 亮阈值 | 10824 | 11081 | 11168 | 14604 |
| get_statistics | 1440 | 3066 | 3200 | 3267 |
| get_histogram | 1473 | 3166 | 3314 | 18636 |
| 3 个标记窗口剖面 | 57 | 75 | 164 | 1749 |
| 射影拟合 + 查表 | 64 | 92 | 184 | 46286 |
| struct.pack + UART1.write | 1645 | 3200 | 3331 | 3423 |
| gc.collect，无分配 | 1891 | 3617 | 3731 | 3808 |
| 分配 640 元素后 gc | 1924 | 3644 | 3770 | 4035 |
| 分配但不主动 gc | 2523 | 4243 | 4358 | 5012 |
| ticks_us | 2 | 2 | 3 | 1531 |

`gc.mem_free()` 从 3,824,224 字节开始，实验后为 2,447,072 字节。自动 GC 事件计数为 1 次。几何探针的电脑端和板端结果最大显示误差小于 0.0001 px，板端日志在 `reports/data/P1-EXP05/geometry-board.log`。

按主循环中使用 profile 检测器的部分估算：

```text
to_numpy_ref + mean
+ 3 个标记窗口剖面
+ 射影拟合 + 查表
+ struct.pack + UART1.write
= 0.540 + 0.075 + 0.092 + 3.200 = 3.907 ms p95
```

在 4 ms 目标内。`find_blobs` 暗/亮阈值都超过 11 ms，不适合放入 90 fps 主循环。`sum + box filter + argmax` 的 p95 约 2.09 ms，且有一次 78.96 ms 离群值。`gc.collect()` p95 约 3.6 ms，应放到受控间隔，不能每帧调用。
