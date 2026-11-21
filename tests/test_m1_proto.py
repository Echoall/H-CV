from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).parents[1] / "k230" / "hcv"))
from proto import INV, RxParser, pack_ball_into, pack_status_into  # noqa: E402


def test_m1_ball_encoder_matches_icd_vector():
    output = bytearray(19)
    pack_ball_into(output, 0x2A, 0x1234, 0x05, 200, 523, 520, -1234, 18, INV)
    assert output.hex(" ") == (
        "a5 5a 0f 01 2a 34 12 05 c8 0b 02 08 02 2e fb 12 00 80 77"
    )


def test_m1_status_encoder_matches_icd_vector():
    output = bytearray(18)
    pack_status_into(output, 7, 2, 0, 897, 31, 23, 2, 0xBEEF, 0x0100)
    assert output.hex(" ") == (
        "a5 5a 0e 02 07 02 00 81 03 1f 00 17 02 ef be 00 01 dd"
    )


def test_m1_rx_parser_decodes_run_info_and_ping():
    parser = RxParser()
    run_info = bytes.fromhex(
        "a5 5a 0d 81 10 05 02 39 30 00 00 00 00 6a ff 01 a3"
    )
    ping = bytes.fromhex("a5 5a 05 82 11 01 00 00 c4")
    assert parser.feed(run_info[:5]) == []
    assert parser.feed(run_info[5:] + ping) == [
        (0x81, 0x10, 5, 2, 12345, 0, -150, 1),
        (0x82, 0x11, 1, 0),
    ]
    assert parser.crc_errors == 0


def test_m1_rx_parser_forwards_ball_sequence():
    parser = RxParser()
    output = bytearray(19)
    pack_ball_into(output, 7, 11, 0, 0, INV, INV, INV, 0, INV)
    assert parser.feed(output) == [(0x01, 7)]
