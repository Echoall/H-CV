"""Recalculate loopback acceptance from the recorded P0-T03 progress logs."""

import argparse
import json
from pathlib import Path


BAUDS = (115200, 460800, 921600)


def summarize(raw):
    last = {}
    for line in raw.splitlines():
        if line.startswith("HCV_UART,"):
            baud, frames, sent, received, mismatch, timeouts, short = map(int, line.split(",")[1:])
            last[baud] = {
                "frames": frames, "tx_bytes": sent, "rx_bytes": received,
                "mismatch": mismatch, "timeout": timeouts, "short_write": short,
                "passed": frames == 10000 and sent == received and not (mismatch or timeouts or short),
            }
    return {baud: last.get(baud, {"passed": False, "reason": "no data"}) for baud in BAUDS}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.raw.read_text(encoding="utf-8", errors="replace")), indent=2))
