"""Compare compressed H.264 picture payloads from a board clip and VLC recording."""

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess


def picture_digests(path, ffmpeg):
    command = [
        str(ffmpeg), "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0",
        "-c:v", "copy", "-bsf:v", "h264_mp4toannexb,filter_units=pass_types=1|5",
        "-f", "framehash", "-hash", "sha256", "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=True)
    return [line.rsplit(",", 1)[-1].strip() for line in result.stdout.splitlines()
            if line and not line.startswith("#")]


def compare(board, pc):
    board_counts = Counter(board)
    pc_counts = Counter(pc)
    pc_unique = {digest: index for index, digest in enumerate(pc) if pc_counts[digest] == 1}
    anchors = [(index, pc_unique[digest]) for index, digest in enumerate(board)
               if board_counts[digest] == 1 and digest in pc_unique]
    result = {"board_pictures": len(board), "pc_pictures": len(pc),
              "unique_matches": len(anchors), "status": "insufficient_overlap"}
    if len(anchors) < 2:
        return result
    if any(right[1] <= left[1] for left, right in zip(anchors, anchors[1:])):
        result["status"] = "inconsistent_order"
        return result
    board_first, pc_first = anchors[0]
    board_last, pc_last = anchors[-1]
    board_overlap = Counter(board[board_first:board_last + 1])
    pc_overlap = Counter(pc[pc_first:pc_last + 1])
    result.update(
        status="compared",
        board_overlap_indices=[board_first, board_last],
        pc_overlap_indices=[pc_first, pc_last],
        missing_board_pictures=sum((board_overlap - pc_overlap).values()),
        unmatched_pc_pictures=sum((pc_overlap - board_overlap).values()),
        repeated_payloads=any(count > 1 for count in board_overlap.values())
        or any(count > 1 for count in pc_overlap.values()),
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", type=Path)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    comparison = compare(picture_digests(args.board, args.ffmpeg), picture_digests(args.recording, args.ffmpeg))
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    return int(comparison["status"] != "compared")


if __name__ == "__main__":
    raise SystemExit(main())
