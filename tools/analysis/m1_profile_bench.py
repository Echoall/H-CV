"""Summarize the board profile-reduction benchmark."""

import argparse
import json
from pathlib import Path


def parse(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("BENCH,"):
            continue
        values = {}
        for field in line.split(",")[1:]:
            key, value = field.split("=", 1)
            values[key] = value if key == "method" else int(value)
        rows.append(values)
    if not rows:
        raise ValueError("no BENCH lines found")
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {"methods": parse(args.input)}
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
