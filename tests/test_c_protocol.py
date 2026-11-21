import subprocess
import shutil
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "protocol"))


def test_c_protocol_vectors_and_python_cross_check(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not found")
    root = Path(__file__).parents[1]
    harness = tmp_path / "harness.c"
    executable = tmp_path / "harness.exe"
    harness.write_text(
        r'''
#include <stdio.h>
#include <string.h>
#include "hcv_proto.h"

int main(void)
{
    static const unsigned char ball[] = {
        0xa5,0x5a,0x0f,0x01,0x2a,0x34,0x12,0x05,0xc8,0x0b,
        0x02,0x08,0x02,0x2e,0xfb,0x12,0x00,0x80,0x77
    };
    static const unsigned char status[] = {
        0xa5,0x5a,0x0e,0x02,0x07,0x02,0x00,0x81,0x03,0x1f,
        0x00,0x17,0x02,0xef,0xbe,0x00,0x01,0xdd
    };
    static const unsigned char run_info[] = {
        0xa5,0x5a,0x0d,0x81,0x10,0x05,0x02,0x39,0x30,0x00,
        0x00,0x00,0x00,0x6a,0xff,0x01,0xa3
    };
    static const unsigned char cmd[] = {0xa5,0x5a,0x05,0x82,0x11,0x01,0x00,0x00,0xc4};
    unsigned char out[32];
    hcv_ball_t b = {0x1234,5,200,523,520,-1234,18,-32768};
    hcv_status_t s = {2,0,897,31,23,2,0xbeef,0x0100};
    hcv_run_info_t r = {5,2,12345,0,-150,1};
    hcv_cmd_t c = {1,0};
    unsigned int value;
    size_t n, i;
    hcv_parser_t parser;
    hcv_frame_t frame;
    hcv_parser_init(&parser);
    for (i = 0; i < sizeof(ball); ++i) hcv_parser_feed(&parser, ball[i], &frame);
    if (parser.frames != 1 || frame.msgid != 1 || frame.seq != 0x2a) return 3;
    hcv_parser_init(&parser);
    for (i = 0; i < sizeof(status); ++i) hcv_parser_feed(&parser, status[i], &frame);
    if (parser.frames != 1 || frame.msgid != 2 || frame.seq != 7) return 4;
    hcv_parser_init(&parser);
    for (i = 0; i < sizeof(run_info); ++i) hcv_parser_feed(&parser, run_info[i], &frame);
    if (parser.frames != 1 || frame.msgid != 0x81 || frame.seq != 0x10) return 5;
    hcv_parser_init(&parser);
    for (i = 0; i < sizeof(cmd); ++i) hcv_parser_feed(&parser, cmd[i], &frame);
    if (parser.frames != 1 || frame.msgid != 0x82 || frame.seq != 0x11) return 6;
    n = hcv_encode_ball(0x2a, &b, out, sizeof(out));
    if (n != sizeof(ball) || memcmp(out, ball, n) != 0) return 7;
    n = hcv_encode_status(7, &s, out, sizeof(out));
    if (n != sizeof(status) || memcmp(out, status, n) != 0) return 8;
    n = hcv_encode_run_info(0x10, &r, out, sizeof(out));
    if (n != sizeof(run_info) || memcmp(out, run_info, n) != 0) return 9;
    n = hcv_encode_cmd(0x11, &c, out, sizeof(out));
    if (n != sizeof(cmd) || memcmp(out, cmd, n) != 0) return 10;
    fputs("ENC ", stdout);
    for (i = 0; i < n; ++i) printf("%02x", out[i]);
    putchar('\n');
    hcv_parser_init(&parser);
    while (scanf("%2x", &value) == 1) {
        if (hcv_parser_feed(&parser, (unsigned char)value, &frame)) {
            printf("FRAME %02x %02x ", frame.msgid, frame.seq);
            for (i = 0; i < frame.payload_len; ++i)
                printf("%02x", frame.payload[i]);
            putchar('\n');
        }
    }
    return 0;
}
''',
        encoding="utf-8",
    )
    compile = subprocess.run(
        ["gcc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(root / "protocol/c"),
         str(root / "protocol/c/hcv_proto.c"), str(harness), "-o", str(executable)],
        capture_output=True, text=True,
    )
    assert compile.returncode == 0, compile.stderr
    run = subprocess.run([str(executable)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "ENC a55a058211010000c4"

    import json
    import sys
    vectors = json.loads((root / "protocol/test_vectors.json").read_text(encoding="utf-8"))
    stream = "".join(vector["frame"].replace(" ", "") for vector in vectors).encode("ascii")
    decoded = subprocess.run([str(executable)], input=stream, capture_output=True, text=False)
    assert decoded.returncode == 0, decoded.stderr
    frames = [line.split() for line in decoded.stdout.decode("ascii").splitlines() if line.startswith("FRAME ")]
    assert len(frames) == len(vectors)
    for fields, vector in zip(frames, vectors):
        assert fields[1].lower() == f"{vector['msgid']:02x}"
        assert fields[2].lower() == f"{vector['seq']:02x}"
        assert fields[3].lower() == "".join(vector["frame"].split()[5:-1]).lower()

    import random
    from protocol.python.hcv_proto import StreamParser, encode_run_info
    rng = random.Random(0x51A7C0DE)
    stream = bytearray()
    corrupted = set()
    originals = {}
    for index in range(5000):
        frame = bytearray(encode_run_info(
            index & 0xFF, 7, 2, index, -1000 + index % 2000, 0, 0
        ))
        originals[index] = bytes(frame)
        action = rng.random()
        if action < 0.01:
            corrupted.add(index)
            position = rng.randrange(len(frame))
            frame[position] ^= 1 << rng.randrange(8)
        elif action < 0.012:
            corrupted.add(index)
            del frame[-rng.randint(1, 4):]
        stream.extend(frame)
    py_parser = StreamParser()
    py_frames = []
    for offset in range(0, len(stream), 17):
        py_frames.extend(py_parser.feed(stream[offset:offset + 17]))
    py_records = [(frame.msgid, frame.seq, frame.payload.hex()) for frame in py_frames]
    c_run = subprocess.run(
        [str(executable)],
        input=__import__("binascii").hexlify(stream),
        capture_output=True,
    )
    assert c_run.returncode == 0, c_run.stderr
    c_records = []
    for line in c_run.stdout.decode("ascii").splitlines():
        fields = line.split()
        if fields and fields[0] == "FRAME":
            c_records.append((int(fields[1], 16), int(fields[2], 16), fields[3]))
    assert c_records == py_records
    for index, raw in originals.items():
        record = (raw[3], raw[4], raw[5:-1].hex())
        if index not in corrupted:
            assert record in py_records
        else:
            assert record not in py_records


def test_mcu_example_only_publishes_real_calibrated_measurements(tmp_path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not found")
    source = Path(__file__).resolve().parents[1] / "protocol" / "c"
    harness = tmp_path / "example_check.c"
    executable = tmp_path / "example_check.exe"
    harness.write_text(
        r'''
#include <assert.h>
#include "hcv_proto.h"

void protocol_init(void);
void uart_rx_interrupt(uint8_t byte);
static unsigned samples;

void control_accept_ball(int16_t position, int16_t velocity, uint16_t t_cap_ms)
{
    assert(position == 520 && velocity == -1234 && t_cap_ms == 65532u);
    ++samples;
}

int main(void)
{
    const uint8_t statuses[] = {0x01, 0x06, 0x04, 0x07, 0x85, 0x05, 0x25, 0x05};
    const unsigned counts[] = {0, 0, 0, 0, 0, 1, 2, 2};
    hcv_ball_t ball = {65532u, 0, 240, 523, 520, -1234, 12, HCV_INV};
    uint8_t bytes[19];
    size_t item, index, length;

    protocol_init();
    for (item = 0; item < sizeof(statuses); ++item) {
        ball.status = statuses[item];
        if (item == 7)
            ball.pos_filt = HCV_INV;
        length = hcv_encode_ball((uint8_t)item, &ball, bytes, sizeof(bytes));
        assert(length == sizeof(bytes));
        for (index = 0; index < length; ++index)
            uart_rx_interrupt(bytes[index]);
        assert(samples == counts[item]);
    }
    return 0;
}
''', encoding="utf-8",
    )
    compile_result = subprocess.run(
        ["gcc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(source),
         str(source / "hcv_proto.c"), str(source / "example_mcu.c"), str(harness),
         "-o", str(executable)],
        capture_output=True, text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    subprocess.run([str(executable)], check=True)
