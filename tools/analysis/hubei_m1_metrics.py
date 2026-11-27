"""Recompute complete-window percentiles and full-loop loss from archived PC logs."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from station.bench.hubei_m1 import metrics, windows


def fields(lines, prefix):
    line, = (line for line in lines if line.startswith(prefix + ","))
    return dict(field.split("=") for field in line.split(",")[1:])


def summarize(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    fps = int(fields(lines, "HCV_REPLAY")["fps"])
    stats = metrics(windows(lines), fps)
    end, = (line.split(",") for line in lines if line.startswith("HCV_DUAL_END,"))
    count, elapsed = map(int, end[1:])
    output = fields(lines, "HCV_REPLAY_END")
    m1 = fields(lines, "HCV_M1_SUMMARY")
    if int(output["ball"]) != count or int(output["status"]) < 1 or int(output["video_ticks"]) < 1:
        raise ValueError("incomplete replay evidence")
    gc_us = sum(int(dict(x.split("=") for x in line.split(",")[1:])["gc_us"])
                for line in lines if line.startswith("HCV_M1_ROUND,"))
    stats.update(full_frames=count, full_wall_us=elapsed, full_drops=max(0, elapsed * fps // 1_000_000 - count),
                 gc_us=gc_us, budget90_us=1_000_000 / 90,
                 found=int(m1["found"]), valid=int(m1["valid"]),
                 ball=int(output["ball"]), status=int(output["status"]), video_ticks=int(output["video_ticks"]))
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", type=Path, nargs="+")
    print(json.dumps({path.name: summarize(path) for path in parser.parse_args().logs}, indent=2))
