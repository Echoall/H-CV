# M3 断流定位

板端代码 `c60f625`，幻尔 CanMV `v1.4-19-ga7de1c8`，MicroPython `1.21.0`。电脑确认重新枚举，板上无旧媒体标志。推流用手机 2.4 GHz 热点、960×540、30 fps、CBR 2 Mbit/s、GOP 30、物理地址发送。

- 同一次上电只启动一个媒体会话，只有一个 FFmpeg RTSP/TCP 客户端。FFmpeg 在 22:36:29.149 启动，于 22:36:34.344 收到 EOF 并结束，历时 5.195 秒。录像有 133 帧，约 3.83 秒。初次和后续客户端之间的相互影响无法解释本轮失败。
- EOF 后板端持续返回发送成功，取包错误为 0，每秒约 90 包，累计 5,402 包、15,015,891 字节。板端 60 秒正常退出，随后串口 `info` 通过。
- 同一次上电，媒体结束后用同一网络、同一 TCP 端口测试普通 socket，不启动媒体。电脑在 33.272 秒内收到全部 8,000,000 字节，数据校验无差异。板端报告 8,000,000 字节、33.159 秒后正常关闭。未观察到对普通 TCP 连接的固定 5 秒断开。

### 普通 TCP 补测

| TCP 数据方向 | 目标速率 | 结果 | 日志 |
| --- | --- | --- | --- |
| 电脑 → K230，电脑监听 | 2.0 Mbit/s | 电脑调用 `sendall` 时连接中止。板端 3.092 秒累计收到 357,300 字节。 | [反向第 1 次](reverse-tcp-2m-first.log) |
| 电脑 → K230，电脑监听 | 2.0 Mbit/s | 主机触发传输时限，板端 42.516 秒累计收到 6,205,480 字节。 | [反向第 2 次](reverse-tcp-2m-repeat.log) |
| 电脑 → K230，电脑监听 | 0.8 Mbit/s | 板端 32.056 秒收到全部 3,200,000 字节，校验无差异。 | [反向低速](reverse-tcp-08m.log) |
| K230 → 电脑，板端监听 | 2.0 和 0.8 Mbit/s | 两次板端均报告 `OSError` 104，主机未取得传输统计。尚未定位调用点。 | [正向 2.0](forward-tcp-2m-repeat.log)、[正向 0.8](forward-tcp-08m.log) |

补测表明同一条热点路径可以出现独立于 RTSP 的普通 TCP 传输问题。正向与反向的发起端和负载方向均有变化。未抓包，无法定位中断是手机热点、板端 Wi-Fi、电脑网络栈还是应用套接字导致。第二次反向记录由测试端的时限结束，不能计作网络主动断开。Windows WLAN 事件日志在测试时段未见记录，无法据此确认无线侧状态。

从仓库根目录复现普通 TCP 对照：

```powershell
python reports/data/M3/m3-20261122-eof/tcp-control-host.py
python reports/data/M3/m3-20261122-eof/tcp-control-host.py --reverse <HOST_IP>
python reports/data/M3/m3-20261122-eof/tcp-control-host.py --reverse <HOST_IP> --block-size 10000
```

### 接收工具本地对照

MediaMTX 在本机环回地址提供 320×192 合成 RTSP 流。FFmpeg TCP、FFmpeg UDP、VLC TCP 分别解码 29.70、29.97、29.87 秒。[回放统计](comparison-local.json)。FFmpeg trace 保留 `Session` 超时参数和 `GET_PARAMETER` 等控制消息，Session ID 已脱敏。

时刻为 UTC+8。原始板端日志去除了固件换行与传感器名称中的 NUL 填充，主机日志已清除网络地址及本机路径。录像留本地。
