"""Collect P0-T02 real-board evidence after USB REPL access is checked."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

if __package__:
    from .board import require_backup
else:
    from board import require_backup


CLI = Path(__file__).with_name("board.py")
REMOTE = "/sdcard/hcv/p0_t02"
TRIALS = 20
LINES = 1000


def verify(port, output, stage):
    require_backup()
    output.mkdir(parents=True, exist_ok=False)

    def board(*args, name, expect_success=True, timeout=90):
        command = [sys.executable, str(CLI), "--port", port, *map(str, args)]
        recorded = ["python", "tools/board/board.py", "--port", port,
                    *(arg.name if isinstance(arg, Path) else str(arg) for arg in args)]
        started = time.monotonic()
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout)
            stdout, stderr, exit_code = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or b""
            stderr = (error.stderr or b"") + b"\nHost subprocess timed out.\n"
            exit_code = None
        (output / (name + ".stdout")).write_bytes(stdout)
        (output / (name + ".stderr")).write_bytes(stderr)
        with (output / "commands.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({
                "command": recorded,
                "exit_code": exit_code,
                "seconds": round(time.monotonic() - started, 3),
            }, ensure_ascii=False) + "\n")
        if exit_code is None or (expect_success and exit_code):
            raise RuntimeError(name + " failed; see " + str(output))
        return result

    with tempfile.TemporaryDirectory(prefix="hcv-p0-") as directory:
        work = Path(directory)
        starter = work / "start.py"
        starter.write_text(
            f"with open('{REMOTE}/payload.py', 'r') as f:\n    exec(f.read())\n",
            encoding="utf-8",
        )
        payload = work / "payload.py"
        received = work / "received.bin"
        board("info", name="info")
        if stage in ("all", "transfer"):
            for trial in range(1, TRIALS + 1):
                expected = f"P0-T02 trial {trial}\n".encode()
                payload.write_text(
                    f"with open('{REMOTE}/result.bin', 'wb') as f:\n"
                    f"    f.write({expected!r})\n"
                    f"for i in range({LINES}):\n    print(i)\n",
                    encoding="utf-8",
                )
                prefix = f"trial-{trial:02}"
                board("put", payload, REMOTE + "/payload.py", name=prefix + "-put")
                board("run", starter, "--timeout", "30", "--stream",
                      "--log", output / (prefix + ".log"), name=prefix + "-run")
                board("get", REMOTE + "/result.bin", received, name=prefix + "-get")
                actual = received.read_bytes()
                (output / (prefix + "-get.bin")).write_bytes(actual)
                lines = (output / (prefix + ".log")).read_bytes().splitlines()
                if actual != expected or lines != [str(index).encode() for index in range(LINES)]:
                    raise RuntimeError(prefix + ": transferred result or print sequence differs")
                print(f"{trial}/{TRIALS}: result matched, {LINES} lines", flush=True)

        if stage in ("all", "timeouts"):
            cooperative = (
                "import os, time\n"
                "deadline = time.ticks_add(time.ticks_ms(), 30000)\n"
                "while True:\n"
                "    if hasattr(os, 'exitpoint'):\n"
                "        os.exitpoint()\n"
                "    if time.ticks_diff(deadline, time.ticks_ms()) <= 0:\n"
                "        print('HCV_TIMEOUT_GUARD')\n"
                "        break\n"
            )
            for name, source in (
                ("cooperative-busy", cooperative),
                ("sleep", "import time\ntime.sleep(100)\n"),
            ):
                blocked = work / (name + ".py")
                blocked.write_text(source, encoding="utf-8")
                started = time.monotonic()
                stopped = board("run", blocked, "--timeout", "5",
                                name="timeout-" + name, expect_success=False)
                elapsed = time.monotonic() - started
                if (stopped.returncode != 1 or b"board script timed out" not in stopped.stderr
                        or b"HCV_TIMEOUT_GUARD" in stopped.stdout or elapsed >= 25):
                    raise RuntimeError(name + ": 5-second timeout recovery was not verified")
                board("info", name="after-timeout-" + name)
                print(f"{name}: timeout recovered and the following info succeeded.")



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--stage", choices=("all", "transfer", "timeouts"), default="all")
    parser.add_argument("--output", type=Path, required=True,
                        help="new directory for raw logs")
    args = parser.parse_args()
    try:
        verify(args.port, args.output, args.stage)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"P0-T02: {error}\n")


if __name__ == "__main__":
    main()
