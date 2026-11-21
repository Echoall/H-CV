import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from protocol.python.hcv_proto import (
    MSG_BALL,
    StreamParser,
    decode_ball,
    encode_cmd,
    encode_run_info,
)


def write_frame(target, frame):
    target.write(frame)
    target.flush()


def run_actions(target, actions, seq):
    for action in actions:
        time.sleep(float(action.get("delay_s", 0)))
        kind = action["type"]
        if kind == "run_info":
            write_frame(target, encode_run_info(
                seq, action.get("test_id", 0), action.get("run_state", 0),
                action.get("elapsed_ms", 0), action.get("target_pos", -32768),
                action.get("rod_angle", -32768), action.get("car_flags", 0),
            ))
        elif kind == "cmd":
            write_frame(target, encode_cmd(seq, action["cmd"], action.get("arg", 0)))
        else:
            raise ValueError("unknown action type: " + kind)
        seq = (seq + 1) & 0xFF
    return seq


def main():
    parser = argparse.ArgumentParser(description="Run a deterministic MCU-side HCV protocol simulation.")
    parser.add_argument("--port", default="-", help="serial port, or - for stdout/stdin")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--script", help="JSON array of run_info/cmd actions")
    args = parser.parse_args()
    actions = json.loads(open(args.script, encoding="utf-8").read()) if args.script else [
        {"type": "run_info", "test_id": 5, "run_state": 1},
        {"type": "cmd", "cmd": 1, "arg": 0},
        {"type": "run_info", "test_id": 5, "run_state": 2},
        {"type": "run_info", "test_id": 5, "run_state": 3, "elapsed_ms": 30000},
    ]

    serial_port = None
    if args.port == "-":
        target = sys.stdout.buffer
        source = sys.stdin.buffer
    else:
        try:
            import serial
        except ImportError:
            raise SystemExit("install pyserial or use --port -")
        serial_port = serial.Serial(args.port, args.baud, timeout=0.1)
        target = serial_port
        source = serial_port

    stream = StreamParser()
    seq = run_actions(target, actions, 0)
    deadline = time.monotonic() + 30
    ball_count = 0
    lost_count = 0
    try:
        while time.monotonic() < deadline:
            data = source.read(4096)
            if not data:
                if args.port == "-":
                    break
                continue
            for frame in stream.feed(data):
                if frame.msgid == MSG_BALL:
                    ball_count += 1
                    ball = decode_ball(frame)
                    if not (ball["status"] & 0x01):
                        lost_count += 1
    finally:
        if serial_port:
            serial_port.close()
    print(json.dumps({
        "sent_actions": len(actions),
        "next_seq": seq,
        "ball_frames": ball_count,
        "ball_lost_or_predicted": lost_count,
        "crc_errors": stream.crc_errors,
        "lost_frames": stream.lost_frames,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
