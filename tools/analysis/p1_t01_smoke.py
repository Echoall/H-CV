import argparse
import hashlib
import json
import statistics
from pathlib import Path


def analyze(directory):
    directory = Path(directory)
    records = [json.loads(path.read_text(encoding="utf-8"))
               for path in sorted(directory.glob("frame_*.json"))]
    timestamps = [record["t_cap_us"] for record in records]
    intervals = [right - left for left, right in zip(timestamps, timestamps[1:])]
    hashes = []
    for record in records:
        image = directory / Path(record["image"]).name
        hashes.append(hashlib.sha256(image.read_bytes()).hexdigest())
    return {
        "directory": str(directory),
        "frames": len(records),
        "interval_us": intervals,
        "interval_median_us": statistics.median(intervals) if intervals else None,
        "estimated_fps": 1_000_000 / statistics.median(intervals) if intervals else None,
        "unique_pixel_hashes": len(set(hashes)),
        "pixel_hashes": hashes,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.directory), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
