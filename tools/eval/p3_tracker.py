"""Generate a reproducible tracker trajectory report and figure."""

import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from k230.hcv.tracker import KalmanTracker


def run(output):
    tracker = KalmanTracker({
        "q_accel": 2000,
        "r_meas": 0.3,
        "gate_mm": 15,
        "coast_max": 3,
        "reacquire_frames": 3,
    })
    rows = []
    position = 0.0
    velocity = 220.0
    for frame in range(180):
        if frame == 70:
            velocity = -velocity
        position += velocity / 90.0
        measurement = position
        if frame == 45:
            measurement += 100.0
        if frame in (92, 93):
            measurement = None
        result = tracker.step(measurement, frame * 11111)
        rows.append({
            "frame": frame,
            "truth_mm": position,
            "measurement_mm": "" if measurement is None else measurement,
            "filtered_mm": result["position_mm"],
            "velocity_mm_s": result["velocity_mm_s"],
            "valid": int(result["valid"]),
            "predicted": int(result["predicted"]),
            "lost": int(result["lost"]),
            "reacquire": int(result["reacquire"]),
        })
    output.mkdir(parents=True, exist_ok=True)
    with (output / "tracker-trajectory.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    frames = [row["frame"] for row in rows]
    plt.figure(figsize=(9, 4))
    plt.plot(frames, [row["truth_mm"] for row in rows], label="truth")
    plt.plot(
        frames,
        [
            None if row["measurement_mm"] == "" else row["measurement_mm"]
            for row in rows
        ],
        ".",
        alpha=0.45,
        label="measurement",
    )
    plt.plot(frames, [row["filtered_mm"] for row in rows], label="filtered")
    plt.axvline(45, color="tab:red", alpha=0.3)
    plt.axvline(70, color="tab:green", alpha=0.3)
    plt.xlabel("frame")
    plt.ylabel("position (mm)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output / "tracker-trajectory.png", dpi=150)
    plt.close()
    (output / "tracker-trajectory.md").write_text(
        "# P3-T01 跟踪器轨迹\n\n"
        "场景包含匀速运动、单帧野值、第 70 帧反弹和连续两帧丢失。"
        "原始数据在 `tracker-trajectory.csv`，图在 `tracker-trajectory.png`。\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    run(ROOT / "reports/data/P3-T01")
