"""Recompute the M1 no-GC allocation probe from its raw board log."""

import argparse
import json
from pathlib import Path


def _fields(line):
    result = {}
    for field in line.strip().split(",")[1:]:
        if "=" in field:
            key, value = field.split("=", 1)
            result[key] = value
    return result


def _number(value):
    try:
        return int(value)
    except ValueError:
        return float(value)


def analyze(path):
    begin = None
    end = None
    stages = {}
    details = {}
    frames = 0
    with Path(path).open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if line.startswith("HCV_M1_ALLOC_BEGIN,"):
                begin = {
                    key: _number(value)
                    for key, value in _fields(line).items()
                    if key != "gc"
                }
            elif line.startswith("HCV_M1_ALLOC_END,"):
                end = {key: _number(value) for key, value in _fields(line).items()}
            elif line.startswith("HCV_M1_ALLOC_STAGE,"):
                fields = _fields(line)
                name = fields.pop("stage")
                stages[name] = {
                    key: _number(value) for key, value in fields.items()
                }
                frames = max(frames, int(stages[name].get("frames", 0)))
            elif line.startswith("HCV_M1_ALLOC_DETAIL,"):
                fields = _fields(line)
                name = fields.pop("stage")
                details[name] = {
                    key: _number(value) for key, value in fields.items()
                }
    if begin is None or end is None or not stages:
        raise ValueError("allocation probe markers are incomplete")
    return {
        "begin": begin,
        "end": end,
        "frames": frames,
        "stages": stages,
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(analyze(args.input), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    print(result, end="")


if __name__ == "__main__":
    main()
