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
    rounds = []
    memory = []
    round_samples = []
    boundary = None
    fps = None
    with Path(path).open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if line.startswith("HCV_M1_BEGIN,"):
                fps = int(_fields(line)["mode"].rsplit("x", 1)[-1])
                samples.clear()
                summaries.clear()
                rounds.clear()
                memory.clear()
                round_samples.clear()
                boundary = None
            elif line.startswith("HCV_M1_SAMPLE,"):
                sample = _fields(line)
                samples.append(sample)
                round_samples.append(sample)
            elif line.startswith("HCV_M1_ROUND,"):
                item = {key: _number(value) for key, value in _fields(line).items()}
                for stage in ("detect", "process", "total"):
                    values = [_quartets(sample, stage) for sample in round_samples]
                    item[f"{stage}_p99_max_us"] = max((value[2] for value in values), default=0)
                    item[f"{stage}_max_us"] = max((value[3] for value in values), default=0)
                rounds.append(item)
                round_samples.clear()
            elif line.startswith("HCV_M1_MEM,"):
                item = _fields(line)
                memory.append({
                    key: value if key == "phase" else int(value)
                    for key, value in item.items()
                })
            elif line.startswith("HCV_M1_SUMMARY,"):
                summaries.append(_fields(line))
            elif line.startswith("HCV_M1_BOUNDARY_TIMING,"):
                boundary = {key: int(value) for key, value in _fields(line).items()}
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
    if fps and rounds and all("active_us" in item for item in rounds):
        for item in rounds:
            expected = item["active_us"] * fps // 1_000_000
            item["duration_dropped"] = max(0, expected - item["frames"])
        active_us = sum(item["active_us"] for item in rounds)
        gc_us = sum(item["gc_us"] for item in rounds)
        measured_us = active_us + gc_us
        frames = sum(item["frames"] for item in rounds)
        result.update(
            rounds=rounds,
            active_us=active_us,
            inter_round_gc_us=gc_us,
            inter_round_gc_dropped_est=sum(item["gc_dropped_est"] for item in rounds),
            active_duration_dropped=sum(item["duration_dropped"] for item in rounds),
            duration_dropped_including_gc=max(0, measured_us * fps // 1_000_000 - frames),
        )
    if memory:
        result["memory"] = memory
    if boundary is not None:
        result["boundary_timing_us"] = boundary
    window_maxima = {}
    for stage in (
        "snapshot", "detect", "map", "track", "pack", "uart", "process", "total", "gc"
    ):
        values = [_quartets(sample, stage) for sample in samples if stage in sample]
        if values:
            window_maxima[stage] = dict(zip(
                ("p50", "p95", "p99", "max"), map(max, zip(*values))
            ))
    result["max_window_us"] = window_maxima
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
