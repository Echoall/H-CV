# P0-EXP00 板卡基线盘点

- 板端系统为 RT-Smart，machine 为 k230_canmv_hiwonder。固件 v1.4-19-ga7de1c8，构建日期 2025-11-06，MicroPython 1.21.0。固件对应源码版本见 revision.txt。刷机镜像的原文件名和 SHA-256 待取得。
- 镜头实测识别为 GC2093，默认 CSI2，传感器类型 32。一次 30 帧短测的实际传感器模式为 1280×720@90；CHN0 取回 640×360 GRAYSCALE。调用 Sensor.get_id() 会报 NotImplementedError，用对应固件的 Sensor 实例中提供的 CSI 枚举来记录。
- gc.mem_free 在导入前为 3,916,096 字节，导入并回收后为 3,861,024 字节。
- 有 cv_lite、ulab、machine.WDT、media.vencoder 和 RTSP 模块。没有 cv2。只检查了 WDT API，未启动看门狗。
- 实际图像实例的 to_numpy_ref、find_blobs、get_statistics、get_histogram 均成功调用。其中 to_numpy_ref 返回 ndarray。ulab.numpy.mean(array, axis=0) 在板上返回预期结果。
- RTSP 类有 start、stop、get_rtsp_url，底层 multimedia.rtsp_server 还有会话创建、音视频发送和释放接口。Encoder 有 Create、Start、GetStream、ReleaseStream、Stop、Destroy、SendFrame。盘点仅验证 API 可见，推流与编码行为留待图传阶段。
- 同一块 K230 上完整跑通 40 个共享 ICD 用例，以及配置读取、覆盖和日志脱敏。
- 当前电脑连 5 GHz 信道 157。板端 STA 接口存在，定向扫描未发现目标网络，连接返回 False。夜间相机测量继续用 USB。
- W1 仍待定。原 TF 卡根目录没有 main.py 或 boot.py 可用于比较。超时的软复位尝试未取得 raw REPL 提示，保护期限过后另行连接成功。
- P0 文档按庐山派硬件引脚编写，而板上固件标识为 Hiwonder。实际板卡型号待核对，UART2 电平、引脚和回环均保持未测。

## 证据与复现

原始固件细节、模块及方法、内存采样、配置自测和相机 API 结果见 [P0-EXP00 数据](data/P0-EXP00/)。摄像头短测的帧日志及解析结果见 [P0-EXP01 冒烟数据](data/P0-EXP01/)。

    python tools/board/board.py --port COM3 run k230/exp/exp00_inventory.py --timeout 60 --stream --log inventory.raw.log
    python tools/board/config_smoke.py --port COM3 --log config-smoke.log
    python tools/analysis/exp01_camera.py capture --port COM3 --smoke --output image-api-check
