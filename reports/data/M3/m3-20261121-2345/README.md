# M3 2026-11-21 板上分段探针

环境：幻尔 K230，COM3，固件 `v1.4-19-ga7de1c8`，MicroPython 1.21.0。探针依赖文件先从仓库上传到 `/sdcard/hcv/`，再按 `board.py run` 运行。运行前后没有执行软重启或硬件复位。

## 结果

- 双路相机：PASS。`m3_camera_probe.py` 输出 `HCV_RTSP_CAMERA,chn0=1,chn1=1` 和 `HCV_RTSP_CLEANUP,stage=camera`，退出码 0。随后 `info` 成功。
- 硬件编码：FAIL/未完成。输出到 `HCV_RTSP_STEP,configure_and_bind_chn1` 后在主机超时。`board.py` 的受保护恢复路径返回 raw REPL recovery failed，工具退出码 3。没有继续尝试 reset 或重跑。
- RTSP：未执行，依赖硬件编码探针干净完成。

复现用的 host wrapper 禁止 `board.py` 在异常恢复时执行软重启，仅用于本次分段探针。原始日志和源码 SHA 对照在本目录。`source-hashes-before.csv` 是读回后的原文件，`source-hashes-updated.csv` 是上传后读回的文件。上传后的 `main_hcv.py`、`hcv/stream.py`、`hcv/stream_probe.py` 与仓库 SHA-256 一致。

## 限制

卡点具体是 MediaManager link、bind 信息、底层编码器分配，还是其后资源调用，尚未确定。由于 raw REPL 无法恢复，本轮不做第二次初始化。需要人工断电重接后重新从 `ports`、`info` 开始，下一轮应先设计更细的 bind 前后短探针并保留每一步输出。
