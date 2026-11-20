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

```c
#include "hcv_proto.h"

hcv_parser_t parser;
hcv_frame_t frame;
hcv_parser_init(&parser);

void uart_rx_interrupt(uint8_t byte)
{
    if (hcv_parser_feed(&parser, byte, &frame) &&
        frame.msgid == HCV_MSG_BALL) {
        hcv_ball_t ball;
        if (hcv_decode_ball(&frame, &ball)) {
            /* 只在 ball.status 的 VALID 位为 1 时更新控制量。 */
        }
    }
}
```

`protocol/c/` 是 C99、无 `malloc` 的实现。`example_mcu.c` 展示了 UART 中断中的最小调用方式。

## PC 自测

```text
python -m pytest -q
python tools/sim/uart_sniffer.py --port COM7 --baud 115200 --csv uart.csv
python tools/sim/mcu_sim.py --port COM8 --baud 115200
```

交叉测试会用 gcc 编译 C 实现，并把 `protocol/test_vectors.json` 中的全部向量交给 C 解析器。模糊测试使用固定种子，向 1,000,000 字节随机流中插入完整帧和损坏帧。

## MCU 接入保护

- 只在 `VALID=1` 时更新控制量。
- `PREDICTED` 只能短时使用，丢失持续超过 50 ms 时进入安全动作。
- 连续 100 ms 没有合法 BALL 帧时判定链路故障。
- `CALIB_OK=0` 或 `FAULT=1` 时禁止开始测试。
- 用 `t_cap_ms` 的差值计算时间间隔，并处理 16 位回绕。
