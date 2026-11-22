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
from tools.analysis.m3_vlc_smoke import localhost_source


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


def client_command(arguments, timeout, logfile, wifi):
    try:
        result = subprocess.run(
            arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as error:
        data = (error.output or b"").decode("utf-8", errors="replace")
        logfile.write_text(redact(data, wifi) + f"\nTimeout after {timeout}s\n", encoding="utf-8")
        raise RuntimeError(f"{logfile.stem} timed out after {timeout}s") from None
    data = result.stdout.decode("utf-8", errors="replace")
    logfile.write_text(redact(data, wifi), encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{logfile.stem} exited with status {result.returncode}")
    return data


def check_clients(vlc, ffmpeg, ffprobe, endpoint, address, port, output, recording,
                  wifi, *, vlc_seconds, show_vlc=False):
    port_test = client_command([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        f"Test-NetConnection {IPv4Address(address)} -Port {int(port)} "
        "-InformationLevel Quiet -WarningAction SilentlyContinue",
    ], 8, output / "tcp-port.log", wifi)
    if port_test.strip() != "True":
        raise RuntimeError("RTSP TCP port did not accept a connection")
    client_command([
        str(ffprobe), "-hide_banner", "-v", "debug", "-rtsp_transport", "tcp",
        "-analyzeduration", "2000000", "-probesize", "1500000", "-show_streams",
        "-of", "json", endpoint,
    ], 12, output / "ffprobe.log", wifi)
    client_command([
        str(ffmpeg), "-hide_banner", "-nostdin", "-rtsp_transport", "tcp", "-i", endpoint,
        "-t", "30", "-map", "0:v:0", "-c", "copy", str(recording),
    ], 42, output / "ffmpeg.log", wifi)
    playback = decode(recording, output / "preview.png")
    (output / "ffmpeg-playback.json").write_text(json.dumps(playback) + "\n", encoding="utf-8")
    if playback["seconds"] < 29:
        raise RuntimeError(f"FFmpeg recording decoded only {playback['seconds']:.2f}s")

    vlc_recording = recording.with_name(recording.stem + "-vlc.mp4")
    vlc_command = [
        sys.executable, str(REPO / "tools/analysis/m3_vlc_smoke.py"), "--vlc", str(vlc),
        "--record-url", endpoint, "--record-seconds", str(vlc_seconds),
    ]
    if show_vlc:
        vlc_command.append("--show-video")
    vlc_log = client_command(
        vlc_command + [str(vlc_recording)], vlc_seconds + 15, output / "vlc.log", wifi,
    )
    vlc_playback = decode(vlc_recording, output / "vlc-preview.png")
    for line in vlc_log.splitlines():
        if line.startswith("HCV_VLC_VERSION "):
            vlc_playback["version"] = line.removeprefix("HCV_VLC_VERSION ")
    (output / "vlc-playback.json").write_text(json.dumps(vlc_playback) + "\n", encoding="utf-8")
    return {"ffmpeg": playback, "vlc": vlc_playback}


def run(vlc, ffmpeg, ffprobe, port, show_vlc):
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
                        check_clients, vlc, ffmpeg, ffprobe, endpoint, address, port_text,
                        output, recording, wifi, vlc_seconds=10, show_vlc=show_vlc,
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
            playback = recording_task.result()
        except Exception as error:
            emit(f"HCV_CLIENT_ERROR,{type(error).__name__}: {error}")
            return code or 1
        emit("HCV_PLAYBACK " + json.dumps(playback))
        print("Recording:", recording)
        return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vlc", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--ffprobe", required=True, type=Path)
    parser.add_argument("--port", default="auto")
    parser.add_argument("--show-vlc", action="store_true", help="show VLC video during the test")
    parser.add_argument("--localhost-mediamtx", type=Path, help="use a synthetic source instead of K230")
    parser.add_argument("--output", type=Path, help="new output directory for the synthetic test")
    args = parser.parse_args()
    vlc, ffmpeg, ffprobe = args.vlc.resolve(), args.ffmpeg.resolve(), args.ffprobe.resolve()
    for executable in (vlc, ffmpeg, ffprobe):
        if not executable.is_file():
            parser.error(f"executable does not exist: {executable}")
    if args.localhost_mediamtx:
        if args.output is None:
            parser.error("--localhost-mediamtx requires --output")
        output = args.output.resolve()
        output.mkdir(parents=True)
        with localhost_source(ffmpeg, args.localhost_mediamtx.resolve(), output) as endpoint:
            port = endpoint.rsplit(":", 1)[1].split("/", 1)[0]
            playback = check_clients(vlc, ffmpeg, ffprobe, endpoint, "127.0.0.1", port,
                                     output, output / "received.mp4",
                                     {"ssid": "", "password": ""}, vlc_seconds=30)
        print(json.dumps(playback))
    else:
        raise SystemExit(run(vlc, ffmpeg, ffprobe, args.port, args.show_vlc))
