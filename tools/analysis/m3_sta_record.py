"""Record the K230 STA probe over RTSP and save a redacted USB log."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from ipaddress import IPv4Address
import json
from pathlib import Path
import re
import subprocess
import sys

import cv2


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.analysis.m3_vlc_smoke import record_with_libvlc


def redact(line, wifi):
    for field in ("ssid", "password"):
        if wifi[field]:
            line = line.replace(wifi[field], "<redacted>")
    return re.sub(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", "<ipv4>", line)


def decode(recording, preview):
    clip = cv2.VideoCapture(str(recording))
    frames = 0
    fps = clip.get(cv2.CAP_PROP_FPS)
    try:
        while True:
            ok, frame = clip.read()
            if not ok:
                break
            if frames == 0:
                height, width = frame.shape[:2]
                cv2.imwrite(str(preview), frame)
            frames += 1
    finally:
        clip.release()
    if not frames or fps <= 0:
        raise RuntimeError("could not decode the recording")
    return dict(frames=frames, fps=fps, seconds=frames / fps, width=width, height=height,
                bytes=recording.stat().st_size)


def run(vlc, port):
    if not vlc.is_file():
        raise FileNotFoundError("VLC executable unavailable")
    wifi = json.loads((REPO / "k230/config.local.json").read_text(encoding="utf-8"))["wifi"]
    name = "m3-hotspot-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    output = REPO / "station/logs" / name
    output.mkdir(parents=True)
    recording = REPO / "station/recordings" / (name + ".mp4")
    recording.parent.mkdir(parents=True, exist_ok=True)
    board = [sys.executable, "tools/board/board.py", "--port", port]
    command = board + ["run", "tools/board/m3_ch0_sta_configurable_probe.py",
                       "--stream", "--timeout", "88"]
    endpoint = None
    recording_task = None
    print("Run files:", output, flush=True)
    with (output / "board.log").open("w", encoding="utf-8") as log, ThreadPoolExecutor(1) as worker:
        def emit(line):
            clean = redact(line, wifi)
            print(clean, flush=True)
            print(clean, file=log, flush=True)

        with subprocess.Popen(command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace") as process:
            for line in process.stdout:
                line = line.rstrip("\r\n")
                if line.startswith("HCV_RTSP_PRIVATE,"):
                    _, address, port_text, path = line.split(",", 3)
                    endpoint = f"rtsp://{IPv4Address(address)}:{int(port_text)}/{path}"
                    emit("HCV_STA_ENDPOINT_AVAILABLE")
                    continue
                emit(line)
                if line == "HCV_CH0_CONFIG_READY" and endpoint and recording_task is None:
                    recording_task = worker.submit(
                        record_with_libvlc, vlc, endpoint, recording, seconds=30,
                        diagnostic_log=False,
                    )
            code = process.wait()
        emit(f"HCV_BOARD_EXIT,{code}")
        if code == 0:
            post = subprocess.run(board + ["info"], cwd=REPO, capture_output=True, timeout=15)
            emit(f"HCV_POST_INFO,{post.returncode}")
            code = post.returncode
        if recording_task is None:
            return code or 1
        try:
            version = recording_task.result()
        except Exception as error:
            emit("HCV_VLC_ERROR," + type(error).__name__)
            return code or 1
        emit("HCV_VLC_RECORD_COMPLETE," + version)
        playback = decode(recording, output / "preview.png")
        emit("HCV_PLAYBACK " + json.dumps(playback))
        print("Recording:", recording)
        return code or int(playback["seconds"] < 29)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vlc", required=True, type=Path)
    parser.add_argument("--port", default="auto")
    args = parser.parse_args()
    raise SystemExit(run(args.vlc.resolve(), args.port))
