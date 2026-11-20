import subprocess
from pathlib import Path


def test_c_protocol_vectors_and_python_cross_check(tmp_path):
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
