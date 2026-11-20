"""Prepare the selected P0-T03 stage and stream its board log."""

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "tools" / "board" / "board.py"
EXPERIMENT = ROOT / "k230" / "exp" / "p0_t03_uart_loopback.py"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("voltage", "loopback"))
    parser.add_argument("--port", default="auto")
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    base = [sys.executable, str(BOARD), "--port", args.port]
    args.log.parent.mkdir(parents=True, exist_ok=True)
    for name in ("guard.py", "link_uart.py"):
        source = ROOT / "k230" / "hcv" / name
        result = subprocess.run([*base, "put", str(source), "/sdcard/hcv/" + name])
        if result.returncode:
            return result.returncode

    with tempfile.TemporaryDirectory(prefix="hcv-t03-") as directory:
        command = Path(directory) / "uart_bench.py"
        source = EXPERIMENT.read_text(encoding="utf-8")
        command.write_text(f"HCV_UART_MODE = {args.phase!r}\n" + source, encoding="utf-8")
        timeout = "110" if args.phase == "voltage" else "200"
        return subprocess.run([
            *base, "run", str(command), "--timeout", timeout, "--stream", "--log", str(args.log),
        ]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
