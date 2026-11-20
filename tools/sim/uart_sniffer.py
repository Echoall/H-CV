import argparse
import csv
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from protocol.python.hcv_proto import (
    MSG_BALL,
    MSG_CMD,
    MSG_RUN_INFO,
    MSG_STATUS,
    StreamParser,
    decode_ball,
    decode_cmd,
    decode_run_info,
    decode_status,
)


def decoded(frame):
    if frame.msgid == MSG_BALL:
        return decode_ball(frame)
    if frame.msgid == MSG_STATUS:
        return decode_status(frame)
    if frame.msgid == MSG_RUN_INFO:
        return decode_run_info(frame)
    if frame.msgid == MSG_CMD:
        return decode_cmd(frame)
    return {"payload_hex": frame.payload.hex()}


def main():
    parser = argparse.ArgumentParser(description="Decode HCV UART frames in real time.")
    parser.add_argument("--port", default="-", help="serial port, or - for stdin")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--csv", help="optional CSV output path")
    args = parser.parse_args()

    serial_port = None
    if args.port == "-":
        source = sys.stdin.buffer
    else:
        try:
            import serial
        except ImportError:
            raise SystemExit("install pyserial or use --port -")
        serial_port = serial.Serial(args.port, args.baud, timeout=0.2)
        source = serial_port

    csv_file = open(args.csv, "w", newline="", encoding="utf-8") if args.csv else None
    writer = csv.writer(csv_file) if csv_file else None
    if writer:
        writer.writerow(["time_unix", "msgid", "seq", "payload_hex", "decoded"])
    stream = StreamParser()
    try:
        while True:
            chunk = source.read(4096)
            if not chunk:
                if args.port == "-":
                    break
                continue
            for frame in stream.feed(chunk):
                data = decoded(frame)
                row = [time.time(), "0x%02x" % frame.msgid, frame.seq, frame.payload.hex(), repr(data)]
                print(" ".join(map(str, row)), flush=True)
                if writer:
                    writer.writerow(row)
                    csv_file.flush()
    finally:
        if csv_file:
            csv_file.close()
        if serial_port:
            serial_port.close()


if __name__ == "__main__":
    main()
