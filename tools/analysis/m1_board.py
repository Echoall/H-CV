"""Recompute M1 board-run summary from HCV_M1 log lines."""

import argparse
import json
from pathlib import Path


def _fields(line):
    result = {}
    for field in line.strip().split(",")[1:]:
        if "=" not in field:
            continue
        key, value = field.split("=", 1)
        result[key] = value
    return result


def analyze(path):
    samples = []
    summaries = []
    with Path(path).open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if line.startswith("HCV_M1_SAMPLE,"):
                samples.append(_fields(line))
            elif line.startswith("HCV_M1_SUMMARY,"):
                summaries.append(_fields(line))
    if not summaries:
        raise ValueError("no HCV_M1_SUMMARY line found")
    summary = summaries[-1]
    result = {
        "sample_windows": len(samples),
        "summary": {
            key: _number(value)
            for key, value in summary.items()
        },
        "max_sample_process_p95_us": max(
            (_quartets(sample, "process")[1] for sample in samples),
            default=0,
        ),
        "max_sample_total_p95_us": max(
            (_quartets(sample, "total")[1] for sample in samples),
            default=0,
        ),
        "max_sample_gc_us": max(
            (_quartets(sample, "gc")[3] for sample in samples),
            default=0,
        ),
        "max_sample_detect_p95_us": max(
            (_quartets(sample, "detect")[1] for sample in samples),
            default=0,
        ),
    }
    return result


def _number(value):
    try:
        return int(value)
    except ValueError:
        return float(value)


def _quartets(fields, name):
    values = fields.get(name, "0/0/0/0").split("/")
    return tuple(int(value) for value in values)


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
