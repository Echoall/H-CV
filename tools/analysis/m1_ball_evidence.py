"""Decode an M1 camera/packet recording and export time-aligned evidence."""

import argparse
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from protocol.python.hcv_proto import decode_ball


PACKET_SIZE = 19
VALID = 1


def decode_recording(path, output, first_visible):
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    with path.open("rb") as recording:
        magic, width_text, height_text, frame_text = recording.readline().split()
        if magic != b"HCV1":
            raise ValueError("invalid capture header")
        width, height, count = map(int, (width_text, height_text, frame_text))
        if min(width, height, count) <= 0:
            raise ValueError("capture has no frames")
        image_size = width * height
        record_size = PACKET_SIZE + image_size
        if path.stat().st_size - recording.tell() != record_size * count:
            raise ValueError("capture length does not match its header")
        preview_size = (2 * width, 2 * height + 28)
        preview = cv2.VideoWriter(
            str(output / "roi.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 15, preview_size
        )
        if not preview.isOpened():
            raise RuntimeError("could not open the evidence video")
        try:
            canvas = np.zeros((preview_size[1], preview_size[0], 3), dtype=np.uint8)
            for index in range(count):
                data = recording.read(record_size)
                ball = decode_ball(data[:PACKET_SIZE])
                if ball["seq"] != index % 256:
                    raise ValueError(f"BALL sequence mismatch at frame {index}")
                rows.append({"frame": index, "valid": bool(ball["status"] & VALID), **ball})
                pixels = np.frombuffer(data, dtype=np.uint8, offset=PACKET_SIZE)
                image = cv2.resize(pixels.reshape(height, width), (2 * width, 2 * height))
                canvas[:2 * height] = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
                canvas[2 * height:] = 0
                cv2.putText(
                    canvas, f"Frame {index}", (8, 2 * height + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1,
                )
                preview.write(canvas)
        finally:
            preview.release()

    with (output / "ball.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    valid_frames = [row["frame"] for row in rows if row["valid"]]
    result = {
        "frames": count,
        "valid_frames": len(valid_frames),
        "first_valid_frame": valid_frames[0] if valid_frames else None,
        "frame_interval_max_ms": max(
            ((current["t_cap_ms"] - previous["t_cap_ms"]) & 0xFFFF
             for previous, current in zip(rows, rows[1:])),
            default=0,
        ),
    }
    if first_visible is not None:
        if not 0 <= first_visible < count:
            raise ValueError("first visible frame is outside the recording")
        next_valid = next((index for index in valid_frames if index >= first_visible), None)
        result.update(
            first_visible_frame=first_visible,
            valid_before_visible=sum(index < first_visible for index in valid_frames),
            first_valid_after_visible=next_valid,
            frames_to_valid_including_arrival=(
                next_valid - first_visible + 1 if next_valid is not None else None
            ),
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--first-visible", type=int)
    args = parser.parse_args()
    result = decode_recording(args.capture, args.output, args.first_visible)
    text = json.dumps(result, indent=2) + "\n"
    (args.output / "summary.json").write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
