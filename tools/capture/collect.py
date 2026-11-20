import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def run(command):
    return subprocess.run(command, check=True, text=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description="Collect a finished K230 capture into datasets/.")
    parser.add_argument("--port", required=True)
    parser.add_argument("--mode", choices=("single", "sequence"), default="single")
    parser.add_argument("--frames", type=int, default=3)
    parser.add_argument("--dataset", default="appearance_v1")
    parser.add_argument("--scene", required=True)
    parser.add_argument("--light-id", required=True)
    parser.add_argument("--tube-color", default="unknown")
    parser.add_argument("--truth-x-mm", type=float)
    parser.add_argument("--rod-angle-deg", type=float, default=0.0)
    parser.add_argument("--capture-chn1", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("datasets"))
    args = parser.parse_args()

    remote = "/sdcard/hcv/capture"
    run([
        "mpremote", "connect", args.port, "exec",
        "import sys; sys.path.append('/sdcard/hcv'); "
        "from exp.cap_frames import capture; "
        "capture(mode=%r, count=%d, output_dir=%r, capture_chn1=%r)"
        % (args.mode, args.frames, remote, args.capture_chn1),
    ])

    target = args.output / args.dataset / args.scene
    target.mkdir(parents=True, exist_ok=True)
    run(["mpremote", "connect", args.port, "fs", "cp", "-r", ":" + remote, str(target)])
    metadata = {
        "dataset": args.dataset,
        "scene": args.scene,
        "light_id": args.light_id,
        "tube_color": args.tube_color,
        "truth_x_mm": args.truth_x_mm,
        "rod_angle_deg": args.rod_angle_deg,
        "sensor": {"pixformat": "GRAYSCALE", "framesize": "1280x720", "fps": None},
        "exposure": {"auto": True, "gain_db": None, "exposure_us": None},
        "operator_notes": "",
        "capture_mode": args.mode,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": sorted(str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()),
    }
    (target / "meta.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
