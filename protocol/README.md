# H-CV 协议库

实现依据 `docs/03-接口规范-ICD.md` 的帧格式和 ICD §3.6 测试向量。

## 帧格式

每帧为：

```text
A5 5A LEN MSGID SEQ PAYLOAD CRC8
```

`LEN` 是 `MSGID`、`SEQ` 和 `PAYLOAD` 的总字节数。CRC 是 CRC-8/SMBUS，计算范围从 `LEN` 到 `PAYLOAD` 末尾，`123456789` 的结果为 `F4`。

当前实现了 BALL、STATUS、RUN_INFO 和 CMD。未知 MSGID 会按 LEN 跳过并继续解析。

## Python

```python
from protocol.python.hcv_proto import StreamParser, encode_ball

frame = encode_ball(0, 123, 1, 255, 0, 0, 0, 1, -32768)
parser = StreamParser()
for decoded in parser.feed(frame):
    print(decoded.msgid, decoded.seq, decoded.payload)
```

`hcv_proto.py` 只使用 `struct` 和基础类型，可复制到 CanMV 的 `/sdcard/hcv/` 使用。

## C

`protocol/c/` 是 C99、无 `malloc` 的实现。解析器使用固定的 36 字节接收缓冲（`HCV_MAX_LEN=32` 加帧头、长度和 CRC），`hcv_parser_t` 还包含序号统计数组，合计约 560 字节。它在读到 MSGID 后立即拒绝已知报文的错误 LEN，未知 MSGID 的 LEN 超过 32 也立即拒绝，并从缓冲区继续扫描下一个 `A5 5A`。

[最小 MCU 示例](c/example_mcu.c) 与 `hcv_proto.c` 一起编译。上电调用 `protocol_init()`，把 UART 接收的字节逐个交给 `uart_rx_interrupt()`。由控制组实现 `control_accept_ball(position, velocity, t_cap_ms)`，把样本交给控制任务。若从中断调用，回调只发布样本，控制计算在前台执行。示例覆盖了测量接收与筛选，控制组还需要下文的安全定时器。

## PC 自测

```text
python -m pytest -q
python tools/sim/uart_sniffer.py --port COM7 --baud 115200 --csv uart.csv
python tools/sim/mcu_sim.py --port COM8 --baud 115200
```

交叉测试会用 gcc 编译 C 实现，并把 `protocol/test_vectors.json` 中的全部向量交给 C 解析器。模糊测试使用固定种子，向 1,000,000 字节随机流中插入完整帧和损坏帧。

## BALL 帧语义速查

K230 通过 UART1 发送二进制 BALL，**整帧 19 字节、115200 bps、8N1、3.3 V TTL**。K230 物理 8 脚 TX 接 MCU RX，物理 10 脚 RX 接 MCU TX，物理 6 或 9 脚 GND 接 MCU GND。双方独立供电时只接这三路。字段详见 [ICD §3.1](../docs/03-接口规范-ICD.md)。

| `VALID`（bit 0） | `PREDICTED`（bit 1） | 报文含义 | 控制组用法 |
|---:|---:|---|---|
| 1 | 0 | 本帧有真实测量，`pos_raw`、`pos_filt`、`vel` 有效 | `CALIB_OK=1`、`FAULT=0` 时用 `pos_filt` 和 `vel` 更新控制，`pos_raw` 可留作诊断 |
| 0 | 1 | 本帧缺少测量，`pos_filt` 和 `vel` 是短时外推，`pos_raw = INT16_MIN` | 需要时短时使用外推输出，状态机仍按缺少新测量处理 |
| 0 | 0 | 球丢失，三个字段都是 `INT16_MIN` | 停止用位置更新控制，丢失持续超过 50 ms 时进入安全动作 |
| 1 | 1 | 当前发送端禁止出现的组合 | 拒绝用于控制，记录协议异常 |

- 位置单位 0.1 mm，速度单位 0.1 mm/s，均为有符号小端 `int16`。正向从摆杆中心指向角度控制机构端。`0` 表示中心或零速度，`INT16_MIN` 表示字段无效。
- 用 MCU 本地时钟统计 **100 ms 无合法 BALL** 的链路超时，收到合法丢失帧也能证明链路仍通。计算两次测量的 `dt` 用 `(uint16_t)(new_t_cap_ms - old_t_cap_ms)`，可处理约 65.536 s 的回绕。K230 重启或链路重新建立后首帧重置时间基准。
- `CALIB_OK=0` 或 `FAULT=1` 禁止开跑。当前 MVP 的板上测试中，标定匹配状态通过 `CALIB_OK` 报告。安装后的自检和实车开跑验收仍要按 SOP 执行。
- 当前检测路径需要连续 2 帧找到球才把位置交给跟踪器。已经锁定后，无测量时最多连续外推 3 帧，再发丢失帧。仍在跟踪时，默认连续 3 个一致的门控拒绝测量可在新位置重新锁定。完全丢失后，跟踪器会用首个重新接受的位置初始化，速度从 0 开始。接收端通过 `VALID` 恢复识别重捕获。

### RUN_INFO 与回合

控制组可用 `hcv_encode_run_info()` 发 **17 字节**的 `RUN_INFO`（`0x81`），频率 ≤20 Hz。`run_state` 按 `IDLE → ARMED → RUNNING → FINISHED/ABORTED` 报告，`test_id` 取 1–6 对应题目、7–9 自定义，0 表示无测试。`elapsed_ms` 与车上计时显示一致。未知的 `target_pos` 和 `rod_angle` 填 `INT16_MIN`。每个报文类型的 `SEQ` 分开计数并在 255 后回到 0。发送状态变化后继续发送最新状态，有助于接收侧在通信恢复时重新同步。

当前 K230 用独立的 30 秒**处理回合**组织采样和内存回收。GC 位于相邻处理回合之间。`RUN_INFO` 的比赛状态记录在日志里，当前版本不会用它开关 GC。如果一次 `RUNNING` 跨过 30 秒处理边界，期间可能出现一次 GC 间隔。台架联调时需对照接收时间戳、序号及 CRC 检查 100 ms 链路超时阈值下的行为。
