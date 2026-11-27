# CHN0 官方基线探针记录

2026-11-22 香港时间。主机完成完全断电重接后，COM3 最小打开测试成功，被动监听 10 秒收到 0 字节，`info` 成功。

首轮 CHN0 官方顺序探针已完成 `sensor_configured`、`set_out_bufs`、`media_link`、`media_init`、`encoder_created`、`encoder_started`、`sensor_started`、`HCV_CH0_READY,url=rtsp://0.0.0.0:8554/hcv`。随后因为探针把 `bytearray` 传给官方 `rtspserver_sendvideodata`，发生主机探针类型错误。`HCV_CH0_CLEANUP` 已输出，但原清理代码只删除 Python 的 link 引用，没有调用 `MediaManager._del_link`。

修正 `bytes` 转换后重跑，第二轮在下一次 `MediaManager.link` 卡住并触发 raw REPL 恢复失败。这说明首轮异常路径遗留了媒体 link。已修正清理为显式 `MediaManager._del_link(link)`，提交 `2237a15`。下一次重接后再重跑，不能把第二轮卡点当作 CHN0 官方路径失败。
