import argparse
import csv
import json
from pathlib import Path

import numpy as np


def analyze(path):
    values = {}
    metadata = {}
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            fields = line.strip().split(",")
            if not fields or fields[0] not in ("HCV_EXP05", "HCV_EXP05_META"):
                continue
            if fields[0] == "HCV_EXP05_META":
                metadata[fields[1]] = float(fields[2])
            else:
                values.setdefault(fields[1], []).append(float(fields[3]))
    summary = {}
    for name, samples in values.items():
        data = np.asarray(samples, dtype=float)
        summary[name] = {
            "count": int(data.size),
            "p50_us": float(np.percentile(data, 50)),
            "p95_us": float(np.percentile(data, 95)),
            "p99_us": float(np.percentile(data, 99)),
            "max_us": float(np.max(data)),
        }
    main_steps = (
        "to_numpy_mean", "marker_profiles", "projective_lookup",
        "pack_uart_write",
    )
    summary["main_loop_processing_p95_us"] = sum(
        summary[name]["p95_us"] for name in main_steps if name in summary
    )
    return {"metadata": metadata, "operations": summary}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(args.input)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
