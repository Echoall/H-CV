# M3 手机热点 TCP 实板复测

- 代码 `d43ca96`，固件 `v1.4-19-ga7de1c8`，MicroPython `1.21.0`。
- Windows 在 2026-11-22 22:12:10（UTC+8）重新枚举 K230，板端计时 28,044 ms，旧媒体标志返回 `ENOENT`。本次上电只启动一次媒体会话。
- 运行 `python tools/analysis/m3_sta_record.py --vlc <vlc.exe> --ffmpeg <ffmpeg.exe> --ffprobe <ffprobe.exe> --show-vlc`。推流参数为 960×540、30 fps、CBR 2 Mbit/s、GOP 30，板端发送窗口 60 秒。
- `tcp-port.log` 记录 TCP 端口可达。`ffprobe.log` 记录 SDP 内的 SPS/PPS 及分析出的 SPS、PPS、IDR。`ffmpeg.log` 记录第二个 RTSP 连接在前几秒收到 EOF。
- `ffmpeg-playback.json` 来自对实拍录像逐帧解码。MP4 为 530,515 字节，960×540，144 帧，4.672111 秒。SHA-256 为 `3e201aa7a0d9c9817dd0e2641bff0aaf6e009c58cd9fd5a2bfc2d633ddcbc6ca`。画面和录像仅留本地。
- FFmpeg 于 22:12:54 已结束输出，板端运行日志直到 22:13:47 才结束，随后串口 `info` 正常。OpenCV 解码时另报 `error while decoding MB 28 10, bytestream -5`。目前未确认 EOF 和这一解码错误的原因。

日志已清除网络地址、本机路径和传感器名称的 NUL 填充，行末空白已标准化。
