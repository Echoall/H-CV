# K230 官方示例只读证据

读取时间 2026-11-22 00:02（香港时间）。板卡已通过 `ports` 和 `info`，随后只读列出 `/sdcard/examples/`、`02-Media`、`17-Sensor`，并取回四个示例文件。没有运行官方示例。

本板固件为 CanMV v1.4-19-ga7de1c8。`02-Media/video_encoder.py` 的 `vi_bind_venc_test` 顺序是：`Sensor()`、`reset`、设置输出、`Encoder()`、`SetOutBufs`、`MediaManager.link(sensor.bind_info()['src'], ...)`、`MediaManager.init()`、`encoder.Create`、`encoder.Start`、`sensor.run()`。清理顺序是 `sensor.stop`、释放 link、`encoder.Stop`、`encoder.Destroy`、`MediaManager.deinit`。

`02-Media/rtsp_server.py` 同样先 `SetOutBufs`、`MediaManager.link`、`MediaManager.init`、`encoder.Create`，然后 `encoder.Start`、`sensor.run`。它使用 CHN0/default channel。官方示例没有把 CHN1/90 fps 组合验证为本项目的可用路径，所以只作为调用顺序依据。
