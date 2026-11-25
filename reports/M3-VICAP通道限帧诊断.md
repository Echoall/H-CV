# M3 VICAP 通道限帧诊断

## 方案 6 复核

11,628 个编码包是在约 125 秒内累计产出，约 93 包/秒。约 90 秒是 VLC 退出后板端继续产包的时长。全程 23,474,189 字节，平均约 188 kB/s，与配置的 1.5 Mbit/s 接近。全程平均值无法判断停流附近是否存在码率尖峰。MP4 的 364 个相邻显示时间戳间隔中有 160 个落在 10–12 ms，且 29 个关键帧约每 0.34 秒出现一次。录像并未稳定到 30 fps。

```powershell
station/bench/runtime/ffprobe.exe -v error -select_streams v:0 -show_frames -show_entries frame=best_effort_timestamp_time,key_frame -of json station/bench/runs/20261125-131056-047050-p6-ap-venc30/recording.mp4
```

## 方案 7

先只读核对当前板端固件 `v1.4-19-ga7de1c8` 对应的 [sensor.py](https://github.com/kendryte/canmv_k230/blob/a7de1c8b9a8eb84238fc6a9880d903c308079b30/port/builtin_py/media/sensor.py)。`Sensor._set_chn_fps(chn=0, fps=30)` 修改指定通道的 `_chn_attr[chn].fps`。`Sensor.run()` 在初始化 VICAP 前把每个已启用通道的属性交给驱动。方案 7 在通道配置后、`sensor.run()` 前调用，传感器保持 `1280×720@90`。编码器按限帧后的输入配置 `30→30`。

[Issue #6 的通道决策](https://github.com/Echoall/H-CV/issues/6#issuecomment-7065543321)将生产布局定为 CHN0 接 VENC 做图传，CHN1 采集 640×360 灰度图供检测。此前 CHN1 接 VENC 的路径卡在 `MediaManager.link`。方案 7 按生产布局运行，将 CHN0 限到 30 fps，并同时从 CHN1 取灰度帧验证约 90 fps。[Claude 已同意按此布局实测](https://github.com/Echoall/H-CV/issues/6#issuecomment-7154278671)。

M3 通过后，再把 M1 检测主循环迁到 CHN1，并复验 `mean_pool` 的平均值等价性、90 fps、30 秒回合丢帧情况，以及图传并行时的检测耗时。

1. 在同一个 K230 AP 和 TCP 8554 上先发送 60 秒合成数据，每 100 ms 发 19,000 字节。电脑核对 11,400,000 字节和数据内容。关闭会话后在同一个端口启动媒体。
2. 板端每约一秒记录编码包数、字节数、实际时间间隔和最近包的 PTS。电脑在 VLC 的唯一 RTSP/TCP 会话前接入本地中继，按 RTP 帧到达时刻记录每秒包数、字节数、首末时刻及最大间隔。选择中继是为了用原有 VLC 播放和录像，在同一条会话上直接统计 RTP。统计位置是电脑应用收到完整 RTP 帧的时刻。中继会改变两端的 TCP 缓冲，结果不用于端到端延迟验收。
3. 方案 6 的录像、帧率、画质和清理门槛继续适用。另核对纯 TCP 对照完整性、RTP 统计存在，以及 RTP 接收窗口内至少有 55 秒的灰度通道采样，实取帧率应在 90 fps 的 ±5% 内。板端原始帧数和错误数也写入本地日志。

电脑环回的 FFmpeg、MediaMTX 和 VLC 测试录到 1,790 帧、59.67 秒、`30/1`，中继解析到 18,118 个 RTP 包。纯 TCP 单独在本机按实际速率运行约 60 秒，收齐 11,400,000 字节，校验错误为 0。回环不含实际无线链路、板端 VICAP 或检测采样，一并验收仍需板卡实测。

```powershell
python -m station.bench.bench --simulate <ffmpeg.exe> <mediamtx.exe> --runs <local-test-dir>
python -m pytest -q
```

## 2026-11-25 方案 7 首轮实测

实测版本 `0205fa5`，方案版本 1。板端 CanMV `v1.4-19-ga7de1c8`、MicroPython `1.21.0`，固件镜像哈希未重测。配置为传感器 `1280×720@90`、CHN0 VICAP 限帧 30 fps、VENC `30→30`、960×540、1.5 Mbit/s、GOP 30、RTSP/TCP、200 ms 播放缓冲。CHN1 取 640×360 灰度帧。

| 检查项 | 现场结果 | 结论 |
| --- | --- | --- |
| 60 秒纯 TCP 对照 | 板端 `READY` 后 0.508 秒报 `EAGAIN` 并关闭。电脑约 2.7 秒后才连接到已切换用途的端口，收到 0 字节 | 测试程序提前退出，对照无效 |
| RTSP 协商 | VLC 记录发出 `OPTIONS` 和 `DESCRIBE`，未进入 `SETUP` 或 `PLAY` | FAIL |
| RTP、画质和录像 | 收到 0 个 RTP 包，MP4 无视频轨。用户现场也未看到画面 | FAIL，画质无法评 |
| 板端编码输出 | 125 秒累计 3,848 包、23,312,786 字节，约 30.8 编码包/秒 | 板端出包正常增长，录像帧率无法验收 |
| CHN1 灰度采样 | 124.634 秒取 11,217 帧，均值约 90.0 fps，0 次采样错误 | 板端检测采样通过，因无 RTP，无法验收与收流并行 |
| 结束恢复 | 板端清理及串口 `info` 通过。自动切回原 Wi-Fi 未确认，已提示手动切回 | 串口 PASS，自动切回 FAIL |

纯 TCP 测试出错早于电脑连接。它没有提供可用于判断无线链路的数据。RTSP 在协商阶段停止，当前日志还不足以确认导致协商失败的机制。

板端限帧已在同次测试得到验证。CHN0 的编码输出从方案 6 的约 93 包/秒降至约 30.8 包/秒，平均用量约 186 kB/s，与 1.5 Mbit/s 目标一致。同期 CHN1 实取约 90.0 帧/秒。电脑未收到 RTP，播放端的 30 fps 和连续录制仍待验收。

方案版本 2 改用有独立时限的板端非阻塞 `accept()`。允许暂未有连接的 `EAGAIN`，最多等 45 秒。板端关闭对照服务时会发串口标记，主机据此停掉未建立的对照连接。同时，收包前核对对照服务的标识，避免将已切换到 RTSP 的端口当作对照。媒体阶段由实际拉流建立 TCP 连接，省去提前探测端口的空连接。

版本 2 还会按 `cur_packs` 完整取包，记录每组查询及返回包数，并识别 Annex B IDR。调试台记录服务就绪和首个已提交 IDR 的时刻，再启动 VLC。中继按 CSeq 保存 `DESCRIBE` 请求、响应码和完整脱敏 SDP，播放器失败行单列。`HCV_VENC_PACKS` 原始行保留在串口日志，脱敏汇总按 IDR、非 IDR I 帧、P 帧和头部统计每组包数。

纯 TCP 环回延迟 4 秒再连接，随后 60.007 秒收齐 11,400,000 字节，无校验错误，同端口可以立即复用。VLC 和中继环回录到 1,792 帧、59.73 秒、`30/1`，接收 18,128 个 RTP 包，完整保存 `DESCRIBE` 的 `200` 和脱敏 SDP。回归测试覆盖 `EAGAIN` 后接入、错误服务识别、多包发送及异常时的单次释放、首个 IDR 等待、`DESCRIBE` 响应匹配和 SDP 脱敏。实板复验仍待进行。

[脱敏实测摘要](data/M3/bench/2026-11-25-p7-v1-field-summary.json)。录像和完整日志留在本机。

```powershell
python tools/analysis/m3_bench_field.py station/bench/runs 20261125 --ffprobe station/bench/runtime/ffprobe.exe
```
