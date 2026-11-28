"""Read a saved board session and its player summary without opening USB or a socket."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re


def summarize(board_log, record):
    channels = {"main": [], "encoder": []}
    ready, released = [], []
    for line in board_log.splitlines():
        stamp, _, message = line.partition(" ")
        if message.startswith("HCV_HUBEI_STATUS,"):
            kind = "main"
        elif message.startswith("video frames="):
            kind = "encoder"
        elif message in ("HCV_CH0_CONFIG_READY", "HCV_CH0_CONFIG_CLEANUP"):
            (ready if message.endswith("READY") else released).append(datetime.fromisoformat(stamp))
            continue
        else:
            continue
        match = re.search(r"\bframes=(\d+)\b", message)
        if match:
            channels[kind].append((datetime.fromisoformat(stamp), int(match[1])))
    stages = record.get("stages", {})
    ended = stages.get("vlc", {}).get("ended")
    if ended:
        ended = datetime.fromisoformat(ended)
    observations = {}
    for name, rows in channels.items():
        gaps = [(b[0]-a[0]).total_seconds() for a, b in zip(rows, rows[1:])]
        if any(gap < 0 for gap in gaps):
            raise ValueError("同一路日志时间倒序。")
        observations[name] = dict(samples=len(rows), last_reported_frames=rows[-1][1] if rows else None,
                                  max_observed_interval_s=round(max(gaps), 3) if gaps else None,
                                  last_minus_player_exit_s=round((rows[-1][0]-ended).total_seconds(), 3)
                                  if rows and ended else None)
    return dict(simulation=record.get("simulation"), ready_markers=len(ready), cleanup_markers=len(released),
                cleanup_after_ready=len(ready) == len(released) == 1 and ready[0] < released[0],
                serial_session_exit=stages.get("board-session", {}).get("exit_code"),
                player_exit=stages.get("vlc", {}).get("exit_code"), observed=observations,
                health_after_reconnect="unverified")


def review(log, summary, output):
    result = summarize(log.read_text(encoding="utf-8"), json.loads(summary.read_text(encoding="utf-8")))
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("summary", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(review(args.log, args.summary, args.output))
