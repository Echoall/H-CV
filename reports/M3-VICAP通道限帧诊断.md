# M3 VICAP 通道限帧诊断

## 方案 6 复核

11,628 个编码包是在约 125 秒内累计产出，约 93 包/秒。约 90 秒是 VLC 退出后板端继续产包的时长。全程 23,474,189 字节，平均约 188 kB/s，与配置的 1.5 Mbit/s 接近。全程平均值无法判断停流附近是否存在码率尖峰。MP4 的 364 个相邻显示时间戳间隔中有 160 个落在 10–12 ms，且 29 个关键帧约每 0.34 秒出现一次。录像并未稳定到 30 fps。

```powershell
station/bench/runtime/ffprobe.exe -v error -select_streams v:0 -show_frames -show_entries frame=best_effort_timestamp_time,key_frame -of json station/bench/runs/20261125-131056-047050-p6-ap-venc30/recording.mp4
```

## 方案 7

先只读核对当前板端固件 `v1.4-19-ga7de1c8` 对应的 [sensor.py](https://github.com/kendryte/canmv_k230/blob/a7de1c8b9a8eb84238fc6a9880d903c308079b30/port/builtin_py/media/sensor.py)。`Sensor._set_chn_fps(chn=0, fps=30)` 修改指定通道的 `_chn_attr[chn].fps`。`Sensor.run()` 在初始化 VICAP 前把每个已启用通道的属性交给驱动。方案 7 在通道配置后、`sensor.run()` 前调用，传感器保持 `1280×720@90`。编码器按限帧后的输入配置 `30→30`。

本轮沿用已实测能启动的 CHN0 图传路径，在另一路 CHN1 同时实取灰度帧。生产规格要求 CHN0 检测、CHN1 图传，历史实测卡在 CHN1 的 `MediaManager.link`。这轮用于验证 VICAP 能否只限一路，生产通道布局和完整检测算法并行性能仍待另测。

1. 在同一个 K230 AP 和 TCP 8554 上先发送 60 秒合成数据，每 100 ms 发 19,000 字节。电脑核对 11,400,000 字节和数据内容。关闭会话后在同一个端口启动媒体。
2. 板端每约一秒记录编码包数、字节数、实际时间间隔和最近包的 PTS。电脑在 VLC 的唯一 RTSP/TCP 会话前接入本地中继，按 RTP 帧到达时刻记录每秒包数、字节数、首末时刻及最大间隔。选择中继是为了用原有 VLC 播放和录像，在同一条会话上直接统计 RTP。统计位置是电脑应用收到完整 RTP 帧的时刻。中继会改变两端的 TCP 缓冲，结果不用于端到端延迟验收。
3. 方案 6 的录像、帧率、画质和清理门槛继续适用。另核对纯 TCP 对照完整性、RTP 统计存在，以及 RTP 接收窗口内至少有 55 秒的灰度通道采样，实取帧率应在 90 fps 的 ±5% 内。板端原始帧数和错误数也写入本地日志。

电脑环回的 FFmpeg、MediaMTX 和 VLC 测试录到 1,790 帧、59.67 秒、`30/1`，中继解析到 18,118 个 RTP 包。纯 TCP 单独在本机按实际速率运行约 60 秒，收齐 11,400,000 字节，校验错误为 0。回环不含实际无线链路、板端 VICAP 或检测采样，一并验收仍需板卡实测。

```powershell
python -m station.bench.bench --simulate <ffmpeg.exe> <mediamtx.exe> --runs <local-test-dir>
python -m pytest -q
```
