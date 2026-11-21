import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "protocol"))
from python.hcv_proto import (  # noqa: E402
    INV,
    Frame,
    StreamParser,
    crc8,
    decode_ball,
    decode_cmd,
    decode_frame,
    decode_run_info,
    decode_status,
    encode_ball,
    encode_cmd,
    encode_run_info,
    encode_status,
)


def test_crc_and_official_vectors():
    assert crc8(b"123456789") == 0xF4
    vectors = json.loads(Path("protocol/test_vectors.json").read_text(encoding="utf-8"))
    expected = {
        "BALL valid": "a5 5a 0f 01 2a 34 12 05 c8 0b 02 08 02 2e fb 12 00 80 77",
        "BALL lost": "a5 5a 0f 01 ff f0 ff 00 00 00 80 00 80 00 80 07 00 80 aa",
        "BALL predicted": "a5 5a 0f 01 00 10 00 06 00 00 80 15 fc c4 09 19 38 01 3c",
        "STATUS": "a5 5a 0e 02 07 02 00 81 03 1f 00 17 02 ef be 00 01 dd",
        "RUN_INFO": "a5 5a 0d 81 10 05 02 39 30 00 00 00 00 6a ff 01 a3",
        "CMD PING": "a5 5a 05 82 11 01 00 00 c4",
    }
    for vector in vectors[:6]:
        assert vector["frame"].lower() == expected[vector["name"]]
        assert decode_frame(bytes.fromhex(vector["frame"])).seq == vector["seq"]


def test_all_test_vectors_decode():
    vectors = json.loads(Path("protocol/test_vectors.json").read_text(encoding="utf-8"))
    assert len(vectors) >= 26
    for vector in vectors:
        frame = decode_frame(bytes.fromhex(vector["frame"]))
        assert frame.msgid == vector["msgid"]
        assert frame.seq == vector["seq"]


def test_message_round_trips():
    assert decode_ball(encode_ball(1, 2, 3, 4, 5, 6, -7, 8, 9)) == {
        "seq": 1, "t_cap_ms": 2, "status": 3, "quality": 4,
        "pos_raw": 5, "pos_filt": 6, "vel": -7, "age_ms": 8, "tilt_est": 9,
    }
    assert decode_status(encode_status(1, 2, 3, 4, 5, 6, 7, 8, 9)) == {
        "seq": 1, "state": 2, "err": 3, "fps_x10": 4, "proc_p95_x10": 5,
        "lat_p95_ms": 6, "stream": 7, "calib_id": 8, "fw_ver": 9,
    }
    assert decode_run_info(encode_run_info(1, 2, 3, 4, 5, -6, 7)) == {
        "seq": 1, "test_id": 2, "run_state": 3, "elapsed_ms": 4,
        "target_pos": 5, "rod_angle": -6, "car_flags": 7,
    }
    assert decode_cmd(encode_cmd(1, 2, -3)) == {"seq": 1, "cmd": 2, "arg": -3}


def test_stream_parser_resynchronizes_and_counts_loss():
    parser = StreamParser()
    first = encode_cmd(10, 1, 0)
    second = bytearray(encode_cmd(12, 1, 0))
    second[-1] ^= 0x80
    third = encode_cmd(13, 1, 0)
    unknown = bytes.fromhex("a5 5a 05 90 22 01 02 03 82")
    received = parser.feed(b"\x00\x01" + first + bytes(second) + unknown + third)
    assert [(frame.msgid, frame.seq) for frame in received] == [
        (0x82, 10), (0x90, 0x22), (0x82, 13)
    ]
    assert parser.crc_errors == 1
    assert parser.lost_frames == 2


def test_unknown_message_is_forwarded():
    parser = StreamParser()
    raw = bytes((0xA5, 0x5A, 0x05, 0x90, 1, 2, 3, 4, 0xCB))
    frames = parser.feed(raw)
    assert len(frames) == 1
    assert frames[0].msgid == 0x90
    assert frames[0].payload == b"\x02\x03\x04"


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 31])
def test_one_million_byte_fuzz_stream(chunk_size):
    rng = random.Random(0xC0DE2026)
    stream = bytearray(rng.getrandbits(8) for _ in range(1_000_000))
    expected = []
    for index, seq in enumerate(range(20)):
        position = 20_000 + index * 47_000
        frame = encode_ball(seq, index, 1, 200, index, index, 0, 1, INV)
        stream[position:position + len(frame)] = frame
        expected.append((1, seq))
    damaged = bytearray(encode_cmd(250, 1, 0))
    damaged[-1] ^= 1
    stream[970_000:970_000 + len(damaged)] = damaged

    parser = StreamParser()
    frames = []
    for offset in range(0, len(stream), chunk_size):
        frames.extend(parser.feed(stream[offset:offset + chunk_size]))
    seen = {(frame.msgid, frame.seq) for frame in frames}
    assert all(item in seen for item in expected)
    assert (0x82, 250) not in seen
    assert parser.crc_errors >= 1
