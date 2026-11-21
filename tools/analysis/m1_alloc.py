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
    frame_drift = []
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
            elif line.startswith("HCV_M1_ALLOC_FRAME,"):
                fields = _fields(line)
                if "drift" in fields:
                    frame_drift.append(int(fields["drift"]))
    if begin is None or end is None or not stages:
        raise ValueError("allocation probe markers are incomplete")
    result = {
        "begin": begin,
        "end": end,
        "frames": frames,
        "stages": stages,
        "details": details,
    }
    if frame_drift:
        if len(frame_drift) != frames:
            raise ValueError("allocation probe frame records are incomplete")
        result["frame_drift"] = {
            "min": min(frame_drift),
            "max": max(frame_drift),
            "mean": sum(frame_drift) / len(frame_drift),
        }
    return result


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
