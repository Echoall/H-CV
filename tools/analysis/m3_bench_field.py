"""Extract shareable measurements from local M3 recordings and diagnostic logs."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

from PIL import Image


def read_text(path):
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def video_metrics(path, ffprobe):
    if not path.is_file():
        return {}
    command = [
        str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height,avg_frame_rate,r_frame_rate,duration,nb_frames:format=duration",
        "-of", "json", str(path),
    ]
    data = json.loads(subprocess.check_output(command, encoding="utf-8"))
    if not data["streams"]:
        return {"has_video_stream": False}
    stream = data["streams"][0]
    return {"has_video_stream": True, **stream, "container_seconds": data["format"].get("duration")}


def board_metrics(directory):
    log = read_text(directory / "board.serial.log")
    mode = re.search(r"find sensor .*?output (\d+)x(\d+)@(\d+)", log)
    statuses = re.findall(
        r"(?m)^(\S+) HCV_CH0_CONFIG_STATUS,elapsed_ms=(\d+),[^\r\n]*?"
        r",packets=(\d+),send_result=(-?\d+),last_read_error=(-?\d+)",
        log,
    )
    complete = re.search(r"HCV_CH0_CONFIG_SUMMARY,packets=(\d+),bytes=(\d+)", log)
    result = {
        "sensor_mode": list(map(int, mode.groups())) if mode else None,
        "sampled_status_rows": len(statuses), "cleanup": "HCV_CH0_CONFIG_CLEANUP" in log,
        "raw_repl_recovery_failed": "raw REPL recovery failed" in log,
    }
    if statuses:
        stamp, elapsed, packets, _, _ = statuses[-1]
        result.update(last_status_at=stamp, last_elapsed_ms=int(elapsed), last_packets=int(packets),
                      sampled_error_status_rows=sum(row[3:] != ("0", "0") for row in statuses))
    if complete:
        result.update(packets=int(complete[1]), bytes=int(complete[2]))
    return result


def vicap_metrics(directory, data):
    log = read_text(directory / "board.serial.log")
    tcp_events = re.findall(r"(?m)^(\S+) (HCV_TCP_CONTROL_[A-Z]+)[^\r\n]*", log)
    errno = re.search(r"HCV_TCP_CONTROL_ERROR \[Errno (\d+)\]", log)
    rates = re.search(r"HCV_VENC_FIRMWARE_RATES,(\d+),(\d+)", log)
    pack_groups = re.findall(r"HCV_VENC_PACKS,(\d+),(\d+),(\d+),(\d+),(\d+),(\d+),([01])", log)
    histograms = {name: Counter() for name in ("idr", "intra", "predictive", "header", "other")}
    for _, _, count, i_packs, p_packs, headers, idr in (map(int, row) for row in pack_groups):
        kind = ("idr" if idr else "intra" if i_packs else "predictive" if p_packs
                else "header" if headers else "other")
        histograms[kind][count] += 1
    return {
        "tcp_control": data.get("tcp_control"),
        "tcp_board_events": tcp_events,
        "tcp_board_errno": int(errno[1]) if errno else None,
        "venc_configured_rates": list(map(int, rates.groups())) if rates else None,
        "detector_sample": data.get("detector_sample"),
        "detector_during_rtp": data.get("detector_during_rtp"),
        "rtp_arrival": data.get("rtp_arrival"),
        "rtsp_ready_at": data.get("rtsp_ready_at"),
        "rtsp_first_idr": data.get("rtsp_first_idr"),
        "vlc_error_lines": data["capture"].get("vlc_error_lines"),
        "pack_group_samples": len(pack_groups),
        "pack_count_histograms": histograms,
        "pack_count_mismatches": sum(expected != count for _, expected, count, *_ in pack_groups),
        "network_restore_reason": data["network"].get("restore_reason"),
        "motion_quality": data.get("motion_quality"),
    }


def screen_metrics(directory, latency):
    digests = []
    for screenshot in sorted(directory.glob("latency-*.png")):
        with Image.open(screenshot) as image:
            width, height = image.size
            right = image.crop((width // 2, height // 3, width, height * 2 // 3))
            digests.append(sha256(right.convert("RGB").tobytes()).digest())
    return {
        "screenshot_count": len(digests), "unique_right_center_images": len(set(digests)),
        "clock_pairs_ms": [[item["left_ms"], item["right_ms"]] for item in latency["readings"]],
    }


def summarize(root, date, ffprobe):
    runs = []
    for path in sorted(root.glob(f"{date}-*/summary.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["simulation"]:
            continue
        plan = data["plan"]
        stages = data["stages"]
        if "board-session" not in stages:
            continue
        client = read_text(path.parent / "vlc.stderr.log")
        entry = {
            "run": path.parent.name, "plan": plan["id"], "version": plan["version"],
            "rtsp_request_methods": re.findall(r"Sending request: ([A-Z]+)", client),
            "vlc_10s_no_data": "no data received in 10s" in client,
            "board_telemetry": board_metrics(path.parent),
            "board_stage": stages["board-session"], "client_stage": stages.get("vlc"),
            "post_info_exit_code": stages.get("board-after", {}).get("exit_code"),
            "network_restored": data["network"].get("restored"),
            "capture": {key: data["capture"][key] for key in (
                "frames", "seconds", "transport_observed", "client_end_at", "client_end_seconds",
                "client_end_reason", "latency_screenshots",
            ) if key in data["capture"]},
            "video_track": video_metrics(path.parent / "recording.mp4", ffprobe),
            "passed_by_wizard_v1": data["passed"],
        }
        if plan.get("latency"):
            entry["screen_check"] = screen_metrics(path.parent, data["latency"])
        if plan.get("vicap_stream_limit"):
            entry["vicap"] = vicap_metrics(path.parent, data)
        runs.append(entry)
    return {"date": date, "runs": runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path)
    parser.add_argument("date", help="date prefix in YYYYMMDD")
    parser.add_argument("--ffprobe", default="ffprobe", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{8}", args.date):
        parser.error("date must use YYYYMMDD")
    print(json.dumps(summarize(args.runs, args.date, args.ffprobe), indent=2))


if __name__ == "__main__":
    main()
