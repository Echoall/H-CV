# 湖北队 K230 RTSP 复现

Issue #6 评论 `7181822763` 指定先做板载 AP，只有录制对照失败才做 STA。当前交付为向导、电脑环回证据和固件接口核对。两种网络的实板表现均待现场验证。

## 板端对照

底本为 `VincentttWang/NUEDC_2026` 的 `2026H/firmware_G3507/k230/rtsp_camera_stream.py`，固定版本 `ed079e501579cbe891e8339b6998f3ea53a12428`。移植文件头附 Jianing Wang 的 MIT 许可。保留 1920×1080@60 传感器输入、640×360 YUV420SP、8 个输出缓冲、编码源与目标均为 60 fps、1000 kbit/s、GOP 10。沿用独立线程中阻塞式 `GetStream`、按物理地址发送及底本的第四参数 `1000`。主线程每秒睡眠，约每五秒输出帧数、字节数、耗时和平均帧率。仅使用 CHN0。

| 改动 | 理由 |
|---|---|
| AP 使用已有的本地配置，STA 经单独配置连接随身 Wi-Fi。把网络等待限制为 30 秒 | 比较两种网络，避免连不上时向导无限等待或日志泄露凭据 |
| 在 `SetOutBufs` 与绑定之后调用 `MediaManager.init()`，随后 `Encoder.Create`，退出时释放绑定和媒体池 | 当前 v1.4 固件使用已经过实板验证的缓冲池和绑定生命周期 |
| 会话标志、有限运行时间、退出检查、单包 `finally` 释放、两秒等待线程退出 | 一次上电只启动一次媒体会话。阻塞线程没有退出时报告失败，避免与它同时销毁编码器 |
| 增加向导可读取的就绪、心跳及清理消息 | 对齐串口、播放、截屏与录像。板端不会把热点凭据写进报告 |

## 接口证据

- 本板既有串口记录 `lcd_live_capture_check3.log` 首行显示 `gc2093_csi2`、传感器类型 30、输出 `1920x1080@60`。原文件 SHA-256 为 `a5310ae96834d3b6df86cfc619b27435dc08ac4dbf42b61ba1c7942e0c263b4f`。因此保持上游原生模式。
- 已保存的板端 API 清单包含 `GetStream`、`ReleaseStream` 和 `rtspserver_sendvideodata_byphyaddr`。固件对应的 `media.py` 定义 `ALIGN_UP`，`sensor.py` 的 `set_framesize` 接受并使用 `alignment`，`vencoder.py` 的 `StreamData` 有 `phy_addr`，`GetStream(chn, streamData, timeout=-1)` 成功返回 0。编码器建通道时读取 src/dst、GOP 和码率。见[逐项只读核对和源码哈希](data/M3/bench/2026-11-26-hubei-api.json)、[实板 API 清单](data/M3/m3-20261122-reconnect/api-inventory.log)和[实板固件 revision](data/P0-EXP00/revision.txt)。
- 本次 COM3 可枚举，`board.py info` 未进入 raw REPL。没有复位或启动相机。现场向导每轮在重新上电后先执行 `info` 和只读探针，核对当前固件、驱动返回的模式与 API，再提示「拍」。若核对失败会留下记录并退出。

## 向导与判读

Windows 入口为 `station/bench/湖北RTSP复现.bat`。每次上电均由操作员确认，五声低音后高音启动。第一轮使用板载 AP。第二轮需要电脑与板子同时连接随身 Wi-Fi；只在第一轮具备测试条件且失败时进行。缺少板端响应、用户取消或原网络恢复失败时停止。原始媒体、时钟截图与完整日志保存在本机。

电脑端使用 VLC 3，启用独立实例、RTSP-over-TCP、200 ms 网络和直播缓存及无音频。单一 RTSP 连接同时显示和录像。实板用电脑左侧时钟与右侧回传画面截图计算画面年龄，读数留待有现场图像后填写。连续显示按实际画面变化计时，每约 0.2 秒抽样右侧视频区域，两次变化相隔超过 1 秒就重新累计。镜头需保持能看见跳动的时钟。模拟使用 VLC 的无窗口视频输出，对完整 I420 画面求摘要，重复画面不推进显示时长。

验收逐项检查连续显示 ≥60 秒、可解码的 640×360 视频轨 ≥58 秒、参考帧编号缺口 ≤1%。需确认 TCP、正常清理和播放结尾；实板还要取得时钟截图。帧号按 IDR 分组，整组缺失不能单凭 `frame_num` 定位。三种决策分支给出后续实验方向；两次都失败也不能单独证明固件是唯一根因。

## 电脑复现

从仓库根目录运行。两个参数分别为 FFmpeg 和 MediaMTX 的实际路径。每次生成新的记录目录。短流场景让模拟发布端在第 25 秒停止，接收端走与正常场景相同的逻辑。

```powershell
python -m station.bench.bench --hubei --simulate $ffmpeg $mediamtx
python -m station.bench.bench --hubei --simulate $ffmpeg $mediamtx --simulate-cut ap
python -m station.bench.bench --hubei --simulate $ffmpeg $mediamtx --simulate-cut both
```

每轮统计及原始 MP4 哈希见[电脑模拟记录](data/M3/bench/2026-11-26-hubei-loopback.json)。模拟未使用真实 Wi-Fi 或相机，不能给出实板延迟。
