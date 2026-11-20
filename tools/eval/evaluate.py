import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "k230"))
from hcv.detector import detect_profile


def load_image(scene, record):
    path = Path(record["image"])
    if not path.is_absolute():
        path = scene / path
    if path.suffix == ".npy":
        return np.load(path)
    if path.suffix == ".bin":
        width = int(record["detect_width"])
        height = int(record["detect_height"])
        return np.fromfile(path, dtype=np.uint8).reshape(height, width)
    raise ValueError("unsupported image file " + str(path))


def ideal_detector(record):
    return {
        "valid": "truth_x_px" in record,
        "u_px": float(record.get("truth_x_px", 0.0)),
        "v_px": float(record.get("truth_y_px", 0.0)),
        "quality": 255,
    }


def summarize_errors(errors, valid_frames, total_frames):
    errors = np.asarray(errors, dtype=float)
    absolute = np.abs(errors)
    return {
        "frames": int(total_frames),
        "valid_frames": int(valid_frames),
        "valid_rate": valid_frames / total_frames if total_frames else 0.0,
        "mean_error_px": float(np.mean(errors)) if len(errors) else None,
        "std_error_px": float(np.std(errors)) if len(errors) else None,
        "rms_error_px": float(np.sqrt(np.mean(errors ** 2))) if len(errors) else None,
        "max_abs_error_px": float(np.max(absolute)) if len(errors) else None,
        "p95_abs_error_px": float(np.percentile(absolute, 95)) if len(errors) else None,
    }


def evaluate(dataset, detector_name="profile", mm_per_px=None, output=None):
    dataset = Path(dataset)
    rows = []
    for scene in sorted(path for path in dataset.iterdir() if path.is_dir()):
        for record_path in sorted(scene.glob("frame_*.json")):
            record = json.loads(record_path.read_text(encoding="utf-8"))
            image = None if detector_name == "ideal" else load_image(scene, record)
            started = time.perf_counter()
            result = ideal_detector(record) if detector_name == "ideal" else detect_profile(image)
            elapsed_us = int((time.perf_counter() - started) * 1_000_000)
            truth = record.get("truth_x_px")
            measured = result.get("u_px") if result.get("valid") else None
            error = None if truth is None or measured is None else measured - truth
            rows.append({
                "scene": scene.name,
                "seq": record.get("seq"),
                "truth_x_px": truth,
                "measured_x_px": measured,
                "error_px": error,
                "truth_x_mm": record.get("truth_x_mm"),
                "error_mm": error * mm_per_px if error is not None and mm_per_px else None,
                "valid": int(bool(result.get("valid"))),
                "quality": result.get("quality"),
                "proc_us": elapsed_us,
            })
    valid = [row for row in rows if row["error_px"] is not None]
    errors = np.array([row["error_px"] for row in valid], dtype=float)
    summary = summarize_errors(errors, len(valid), len(rows))
    summary["detector"] = detector_name
    if output:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        with (output / "frames.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [
                "scene", "seq", "truth_x_px", "measured_x_px", "error_px",
                "truth_x_mm", "error_mm", "valid", "quality", "proc_us"
            ])
            writer.writeheader()
            writer.writerows(rows)
        (output / "metrics.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--detector", choices=("ideal", "profile"), default="profile")
    parser.add_argument("--mm-per-px", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary, _ = evaluate(args.dataset, args.detector, args.mm_per_px, args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
