"""Collect P0-T02 real-board evidence after USB REPL access is checked."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


CLI = Path(__file__).with_name("board.py")
REMOTE = "/sdcard/hcv/p0_t02"
TRIALS = 20
LINES = 1000


def verify(port, output):
    output.mkdir(parents=True, exist_ok=False)

    def board(*args, name, expect_success=True):
        command = [sys.executable, str(CLI), "--port", port, *map(str, args)]
        started = time.monotonic()
        result = subprocess.run(command, capture_output=True, timeout=90)
        (output / (name + ".stdout")).write_bytes(result.stdout)
        (output / (name + ".stderr")).write_bytes(result.stderr)
        with (output / "commands.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({
                "command": command,
                "exit_code": result.returncode,
                "seconds": round(time.monotonic() - started, 3),
            }, ensure_ascii=False) + "\n")
        if expect_success and result.returncode:
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
        board("backup", name="backup")
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

        for name, source in (
            ("busy", "while True:\n    pass\n"),
            ("sleep", "import time\ntime.sleep(100)\n"),
        ):
            blocked = work / (name + ".py")
            blocked.write_text(source, encoding="utf-8")
            stopped = board("run", blocked, "--timeout", "5",
                            name="timeout-" + name, expect_success=False)
            if stopped.returncode != 1 or b"board script timed out" not in stopped.stderr:
                raise RuntimeError(name + ": timeout did not produce the expected error")
            board("info", name="after-timeout-" + name)
            print(f"{name}: timeout recovered and the following info succeeded.")



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--output", type=Path, required=True,
                        help="new directory for raw logs")
    args = parser.parse_args()
    try:
        verify(args.port, args.output)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"P0-T02: {error}\n")


if __name__ == "__main__":
    main()
