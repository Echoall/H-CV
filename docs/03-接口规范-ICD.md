# 03 接口规范（ICD）

版本 **ICD v1.0**（2026-11-19，Manager）。本文是 K230 与控制组 MCU、K230 与场外笔记本之间的**唯一权威定义**。
实现必须逐字节遵守。要改动就走变更请求（CR），并把版本号升级（字段只增不改：新增字段用新的报文 ID，或者在末尾追加，同时升级版本号）。

---

## 1. 坐标系与单位

```
   合页端                                        角度控制机构端
  （左）  ──────────────────────O──────────────────────►  +x
   −125mm        −50          0          +50        +125mm
```

- **x 轴**：沿摆杆方向，原点是摆杆中心 O（刻度 0）；**正方向指向角度控制机构一端**，即远离合页的一端，和赛题图 3 的 X 轴一致。图像中通过镜像和翻转设置，让 +x 对应画面右侧。
- **位置单位**：0.1mm，int16。有效范围 ±1250（即 ±125.0mm）。
- **速度单位**：0.1mm/s，int16，向 +x 方向运动为正。
- **摆杆角 θ 单位**：0.01°，int16。**θ > 0 表示控制机构端比合页端高**，此时钢球向 −x 方向滚动。（这个符号约定要和控制组确认，见 07。）
- **无效值**：int16 字段一律用 `INT16_MIN = −32768`（`0x8000`）表示无效或未知。**禁止**用 0 表示"无效"。
- **字节序**：小端。

## 2. 物理层

| 项目 | 规定 |
|---|---|
| K230 端口 | UART2，GH1.25-4P 座（丝印 2）：**GPIO11 = TX → MCU 的 RX**；**GPIO12 = RX ← MCU 的 TX** |
| 电平 | **3.3V TTL**。MCU 端必须是 3.3V；5V 电平会损坏 K230 |
| 参数 | 115200 bps，8N1，无流控。波特率可在 `config.json` 中修改，可选 230400、460800、921600，修改需双方同步 |
| 地线 | **必须共地** |
| 电源脚 | GH1.25 的 V 脚（5V）在双方各自供电时**必须悬空** |

## 3. 帧格式（K230 ↔ MCU 通用）

```
偏移:  0     1     2     3       4      5 … 5+N−1     5+N
     ┌─────┬─────┬─────┬───────┬──────┬────────────┬───────┐
     │ A5  │ 5A  │ LEN │ MSGID │ SEQ  │  PAYLOAD   │ CRC8  │
     └─────┴─────┴─────┴───────┴──────┴────────────┴───────┘
```

- `LEN` = 1(MSGID) + 1(SEQ) + N(PAYLOAD)，也就是从 MSGID 到 PAYLOAD 末尾的字节数。
- `SEQ`：每个发送方、每种 MSGID 单独计数，从 0 递增到 255 后回绕。接收方据此统计丢帧。
- `CRC8`：**CRC-8/SMBUS**（多项式 0x07，初值 0x00，不反射，结果不异或），计算范围是 `LEN` 到 `PAYLOAD` 末尾（不含帧头 A5 5A）。校验值：`CRC8("123456789") = 0xF4`。
- 接收方的解析规则：逐字节寻找 `A5 5A`；读到 LEN 后，如果 LEN 超出该 MSGID 的已知长度，就丢弃并重新同步；CRC 不对就丢弃这一帧。**收到未知的 MSGID 时，按 LEN 跳过，不能报错停机。**

### 3.1 BALL（0x01）K230 → MCU，每处理一帧发送一次（60–90Hz）

PAYLOAD 共 13 字节，整帧 19 字节：

| 偏移 | 字段 | 类型 | 说明 |
|---|---|---|---|
| 0 | `t_cap_ms` | u16 | 这一帧被取到时 K230 的毫秒时钟（低 16 位，约 65.5 s 回绕）。MCU 计算 dt 时要处理回绕 |
| 2 | `status` | u8 | 状态位，见下表 |
| 3 | `quality` | u8 | 检测质量，0–255（越大越可信） |
| 4 | `pos_raw` | i16 | 本帧测得的位置（已补偿、已标定，未滤波）。无测量时为 INT16_MIN |
| 6 | `pos_filt` | i16 | 跟踪器滤波后的位置。丢失时为 INT16_MIN |
| 8 | `vel` | i16 | 跟踪器估计的速度。丢失时为 INT16_MIN |
| 10 | `age_ms` | u8 | 从 `t_cap_ms` 到本帧开始发送的时间，单位 ms，超过 255 时取 255 |
| 11 | `tilt_est` | i16 | 视觉估计的摆杆倾角（0.01°），不可用时为 INT16_MIN（属可选功能 F16） |

`status` 位定义：

| 位 | 名称 | 含义 |
|---|---|---|
| 0 | `VALID` | 本帧有真实测量，`pos_raw` 有效 |
| 1 | `PREDICTED` | 本帧没有测量，`pos_filt` 和 `vel` 是外推值（最多连续外推 `coast_max` 帧，默认 3） |
| 2 | `CALIB_OK` | 已加载与当前配置匹配的标定，且通过了上电自检 |
| 3 | `COMP_FULL` | 用 3 个参考标记完成了倾斜补偿 |
| 4 | `COMP_DEGRADED` | 补偿降级：标记少于 3 个，或者改用了 MCU 回传的摆杆角 |
| 5 | `LOW_CONF` | 质量低于阈值，但仍判为有效（MCU 可以选择降低信任度） |
| 6 | `EXTRAPOLATED` | 位置超出了标定锚点范围，结果是外推得到的 |
| 7 | `FAULT` | 系统故障，原因见 STATUS 报文 |

**三种状态的判定**：

- **有效**：`VALID=1`。三个位置和速度字段都有值。
- **外推**：`VALID=0` 且 `PREDICTED=1`。`pos_raw` 为 INT16_MIN，`pos_filt` 和 `vel` 是预测值。
- **丢失**：`VALID=0` 且 `PREDICTED=0`。三个字段全部为 INT16_MIN。

> **丢失时也要照常发送 BALL 帧**，这样 MCU 能区分"球丢了"和"链路断了"。

**MCU 端必须实现的保护**（写给控制组）：

1. 连续 **100ms** 收不到合法的 BALL 帧，判为**链路故障**，进入安全动作（例如摆杆回水平）。
2. 只在 `VALID=1` 时更新控制量；`PREDICTED` 帧可以短时使用；**丢失**状态持续超过 50ms，进入安全动作。
3. `CALIB_OK=0` 或 `FAULT=1` 时，**禁止开始测试**。
4. 用 `t_cap_ms` 的差值作为 dt，不要用接收时刻的差值。

### 3.2 STATUS（0x02）K230 → MCU，2Hz 发送；收到 PING 时立即回一帧

PAYLOAD 共 12 字节，整帧 18 字节：

| 偏移 | 字段 | 类型 | 说明 |
|---|---|---|---|
| 0 | `state` | u8 | 0 = BOOT，1 = NO_CALIB，2 = READY，3 = DEGRADED，4 = FAULT |
| 1 | `err` | u8 | 错误码（§3.5），0 表示无错误 |
| 2 | `fps_x10` | u16 | 近 1 秒的输出帧率 × 10 |
| 4 | `proc_p95_x10` | u16 | 近 1 秒单帧处理时间的 p95，单位 0.1ms |
| 6 | `lat_p95_ms` | u8 | 端到端延迟（曝光 → 串口发出）的 p95 估计值，ms；由 EXP-09 实测后写进配置，再加上运行时的 age |
| 7 | `stream` | u8 | 0 = 关闭，1 = 等待客户端，2 = 推流中，3 = 推流出错 |
| 8 | `calib_id` | u16 | 当前标定的 ID（标定内容的 CRC16），0 表示没有标定 |
| 10 | `fw_ver` | u16 | 软件版本，高字节是主版本号，低字节是次版本号 |

### 3.3 RUN_INFO（0x81）MCU → K230，≤20Hz（可选，但强烈建议实现）

K230 用它来自动分段录像、在画面上叠加信息、写日志，**并且（可选）用摆杆角做倾斜补偿的后备**。没有这个报文时，K230 也必须能正常工作。

PAYLOAD 共 11 字节，整帧 17 字节：

| 偏移 | 字段 | 类型 | 说明 |
|---|---|---|---|
| 0 | `test_id` | u8 | 0 = 无，1–6 = 题目要求编号，7–9 = 自定义 |
| 1 | `run_state` | u8 | 0 = IDLE，1 = ARMED（已就绪等按键），2 = RUNNING，3 = FINISHED，4 = ABORTED |
| 2 | `elapsed_ms` | u32 | 本次测试已经用了多少毫秒（和车上 2 英寸屏显示的时间一致） |
| 6 | `target_pos` | i16 | 当前目标位置（0.1mm），没有时为 INT16_MIN |
| 8 | `rod_angle` | i16 | MCU 掌握的摆杆角度（0.01°，符号见 §1），未知时为 INT16_MIN |
| 10 | `car_flags` | u8 | bit0 = 行驶中，bit1 = 加速，bit2 = 减速，bit3 = 在弯道上（可选，只用于日志） |

### 3.4 CMD（0x82）MCU → K230（偶尔发送）

PAYLOAD 共 3 字节：`cmd`（u8）+ `arg`（i16）。

| cmd | 含义 | K230 的响应 |
|---|---|---|
| 0x01 | PING | 立即回一帧 STATUS |
| 0x02 | MARK | 在日志和遥测里写一个事件标记，`arg` 由 MCU 自定 |

**K230 不提供任何会改变测量行为的串口命令**（例如重新标定、调阈值），避免误操作。标定只能通过电脑工具完成。

### 3.5 错误码 `err`

| 码 | 含义 |
|---|---|
| 0 | 无错误 |
| 1 | 摄像头初始化失败 |
| 2 | 取帧超时（>200ms 没拿到帧） |
| 3 | 标定文件缺失 |
| 4 | 标定与当前配置不匹配（指纹不一致） |
| 5 | 上电自检失败（参考标记位置偏离标定时超过阈值，可能是摄像头被碰动了） |
| 6 | 参考标记丢失（持续超过 1s） |
| 7 | 处理超时（单帧处理时间 > 3 倍预算） |
| 8 | 内存不足 |
| 9 | 推流出错（不影响测量） |
| 10 | 配置文件错误 |
| 255 | 未知异常（详情见日志） |

### 3.6 测试向量（必须全部通过）

下表是 `protocol/test_vectors.json` 的种子数据，由 Manager 用参考实现算出。

| 用例 | 字段 | 完整帧（十六进制） |
|---|---|---|
| BALL 有效 | seq=0x2A, t=0x1234, status=0x05, q=200, raw=523, filt=520, vel=−1234, age=18, tilt=INV | `A5 5A 0F 01 2A 34 12 05 C8 0B 02 08 02 2E FB 12 00 80 77` |
| BALL 丢失 | seq=0xFF, t=0xFFF0, status=0x00, q=0, raw/filt/vel=INV, age=7, tilt=INV | `A5 5A 0F 01 FF F0 FF 00 00 00 80 00 80 00 80 07 00 80 AA` |
| BALL 外推 | seq=0x00, t=0x0010, status=0x06, q=0, raw=INV, filt=−1003, vel=2500, age=25, tilt=312 | `A5 5A 0F 01 00 10 00 06 00 00 80 15 FC C4 09 19 38 01 3C` |
| STATUS | seq=0x07, state=2, err=0, fps×10=897, proc×10=31, lat=23, stream=2, calib=0xBEEF, ver=0x0100 | `A5 5A 0E 02 07 02 00 81 03 1F 00 17 02 EF BE 00 01 DD` |
| RUN_INFO | seq=0x10, test=5, state=2, elapsed=12345, target=0, angle=−150, flags=0x01 | `A5 5A 0D 81 10 05 02 39 30 00 00 00 00 6A FF 01 A3` |
| CMD PING | seq=0x11, cmd=1, arg=0 | `A5 5A 05 82 11 01 00 00 C4` |

---

## 4. K230 ↔ 笔记本（WiFi）

### 4.1 网络

- **默认模式**：K230 开 **AP（热点）**，SSID 为 `HCV-xxxx`，WPA2 加密，密码写在 `config.local.json` 里（不进仓库），信道可配置（1、6、11）。笔记本直接连这个热点。
- **备用模式**：STA 模式，连接便携路由器（由 EXP-12 决定是否使用）。
- K230 的 IP 地址写在 `config.json` 里，并在 STATUS/遥测中上报。

### 4.2 视频

- `rtsp://<K230_IP>:8554/hcv`，H.264，分辨率、帧率、码率、GOP 由 `config.json` 规定（默认 1280×720、30fps、2000kbps、GOP 15，最终取值由 EXP-12 确定）。

### 4.3 遥测（UDP）

- **登记**：笔记本每 1s 向 `K230_IP:9101` 发一个 UDP 包，内容为 ASCII `HCV-HELLO v1\n`。K230 把遥测发到**最近一次 HELLO 的来源地址**的 **9100** 端口；连续 5s 没收到 HELLO 就停止发送。
- **合规**：K230 只处理 HELLO 包，网络上收到的其他任何内容都直接丢弃。
- **格式**：ASCII，每行一条记录，用 `\n` 分隔。多行打包进一个数据报，每个数据报不超过 1200 字节，每 100ms 发送一次（全帧率数据也只需每秒约 10 次 `sendto`）。

```
T,<seq>,<t_cap_ms>,<status_hex2>,<u_px:.2f>,<pos_raw_mm:.2f>,<pos_filt_mm:.2f>,<vel_mms:.1f>,<quality>,<proc_ms:.2f>,<age_ms>,<test_id>,<run_state>,<elapsed_ms>,<target_mm:.1f>
S,<k230_ms>,<state>,<err>,<fps:.1f>,<proc_p95_ms:.2f>,<lat_p95_ms>,<stream>,<calib_id_hex4>,<fw_ver_hex4>
E,<k230_ms>,<event_name>,<detail>
```

- 无效值写成空字段（例如 `T,12,4660,00,,,,,0,1.20,7,0,0,0,`）。
- `seq` 与 BALL 帧的 SEQ 一致，`t_cap_ms` 写完整的毫秒数（不截成 16 位）。
- 事件 `event_name` 的取值：`BOOT`、`READY`、`RUN_STATE`（detail 写 `test_id:run_state`）、`MARK`、`CALIB_LOADED`、`CALIB_CHECK`、`ERROR`、`STREAM`。
- 笔记本收到 `RUN_STATE` 的 RUNNING/FINISHED 事件后，自动开始或停止分段录像（见 05）。

---

## 5. 文件格式

### 5.1 `config.json`（运行配置，进仓库；机密项写在 `config.local.json`，后者覆盖同名项）

```json
{
  "schema": 1,
  "sensor": {"id": 2, "width": 1280, "height": 720, "fps": 90, "hmirror": false, "vflip": false},
  "chn_detect": {"width": 640, "height": 360, "format": "GRAYSCALE", "crop": null},
  "chn_stream": {"width": 1280, "height": 720, "format": "YUV420SP"},
  "isp": {"ae_lock": true, "exposure_us": 3000, "gain": null, "awb_lock": true},
  "roi": {"x": 0, "y": 150, "w": 640, "h": 48},
  "markers": {"enabled": true, "x_mm": [-110.0, 0.0, 110.0], "search_px": 12, "polarity": "dark"},
  "detector": {"name": "profile", "params": {}},
  "tracker": {"q_accel": 2000.0, "r_meas": 0.3, "gate_mm": 15.0, "coast_max": 3, "reacquire_frames": 3},
  "uart": {"id": 2, "tx_pin": 11, "rx_pin": 12, "baud": 115200},
  "status_hz": 2,
  "latency_const_ms": 0,
  "wifi": {"mode": "ap", "ssid": "HCV-0000", "channel": 6, "password": "SEE_LOCAL"},
  "stream": {"enabled": true, "port": 8554, "path": "hcv", "fps": 30, "bitrate_kbps": 2000, "gop": 15},
  "telemetry": {"enabled": true, "hello_port": 9101, "data_port": 9100, "batch_ms": 100},
  "log": {"level": "info", "run_csv": false},
  "safe_boot": {"button_gpio": null, "skip_file": "/sdcard/hcv/SKIP_AUTOSTART"}
}
```

上面的数值都是占位用的默认值。真实取值由各实验确定，并通过 CR 更新本节。

### 5.2 `calib.json`（标定结果，由标定工具生成，保存在 `/sdcard/hcv/calib.json`，同时备份到电脑上）

```json
{
  "schema": 1,
  "id": 48879,
  "created": "2026-11-20T15:30:00",
  "fingerprint": "sha1 of {sensor, chn_detect, roi, hmirror, vflip, detector.name}",
  "conditions": {"rod_level_deg": 0.0, "light": "L2", "operator_note": ""},
  "anchors": [
    {"x_mm": -120.0, "u_px": 21.37, "n": 30, "std_px": 0.06},
    {"x_mm": -110.0, "u_px": 47.90, "n": 30, "std_px": 0.05}
  ],
  "markers_ref": [
    {"name": "L", "x_mm": -110.0, "u_px": 48.1, "v_px": 12.4},
    {"name": "C", "x_mm": 0.0, "u_px": 320.2, "v_px": 12.1},
    {"name": "R", "x_mm": 110.0, "u_px": 592.6, "v_px": 12.3}
  ],
  "interp": "linear",
  "validation": {"cv_max_err_mm": 0.6, "cv_rms_mm": 0.3, "monotonic": true}
}
```

- `fingerprint` 与当前配置不一致时，**拒绝加载**，状态置为 NO_CALIB，错误码 4。
- `anchors` 必须按 u 严格单调；不单调就判标定失败。

### 5.3 快速复核记录 `check_<时间>.json`

记录每个复核点（0、+50、−50mm）的真值、测量值和误差，以及参考标记的偏移量和是否通过。
**判定**：每个点 |误差| ≤ 1.5mm，并且所有参考标记相对标定时的偏移都不超过 3px，才算通过。
