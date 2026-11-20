"""Recalculate P0-T02 outcomes from the board runs and command logs."""

import argparse
import json
from pathlib import Path


def results(directory):
    commands = [json.loads(line) for line in
                (directory / "commands.jsonl").read_text(encoding="utf-8").splitlines()]
    verified = 0
    line_count = 0
    for trial in range(1, 21):
        prefix = f"trial-{trial:02}"
        log = directory / (prefix + ".log")
        received = directory / (prefix + "-get.bin")
        if not log.is_file() or not received.is_file():
            continue
        lines = log.read_bytes().splitlines()
        if (lines == [str(index).encode() for index in range(1000)]
                and received.read_bytes() == f"P0-T02 trial {trial}\n".encode()):
            verified += 1
            line_count += len(lines)

    timeout = {}
    for record in commands:
        arguments = record["command"]
        if "--timeout" in arguments and arguments[arguments.index("--timeout") + 1] == "5":
            timeout[arguments[5]] = {
                "exit_code": record["exit_code"], "seconds": record["seconds"],
            }
    return {"verified_transfers": verified, "lines_verified": line_count, "timeout": timeout}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(results(parser.parse_args().directory), indent=2))
