"""Record the K230 Wi-Fi probe over RTSP and save a redacted USB log."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from ipaddress import IPv4Address
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.analysis.m3_vlc_smoke import localhost_source


def redact(line, wifi):
    for field in ("ssid", "password"):
        if wifi[field]:
            line = line.replace(wifi[field], "<redacted>")
    line = re.sub(r"(?im)(\bSession:[ \t]*)[^;\s]+", r"\1<session>", line)
    return re.sub(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", "<ipv4>", line)


def decode(recording, preview):
    import cv2

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
    started = datetime.now().astimezone().isoformat(timespec="milliseconds")
    try:
        result = subprocess.run(
            arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as error:
        data = (error.output or b"").decode("utf-8", errors="replace")
        result = None
    else:
        data = result.stdout.decode("utf-8", errors="replace")
    ended = datetime.now().astimezone().isoformat(timespec="milliseconds")
    outcome = f"Timeout after {timeout}s" if result is None else f"Exit status {result.returncode}"
    logfile.write_text(
        f"Start: {started}\n{redact(data, wifi)}\nEnd: {ended}\n{outcome}\n", encoding="utf-8",
    )
    if result is None:
        raise RuntimeError(f"{logfile.stem} timed out after {timeout}s") from None
    if result.returncode:
        raise RuntimeError(f"{logfile.stem} exited with status {result.returncode}")
    return data


def record_ffmpeg(ffmpeg, endpoint, output, recording, wifi, *, log_level="info", transport="tcp"):
    client_command([
        str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", log_level,
        "-rtsp_transport", transport, "-i", endpoint,
        "-t", "30", "-map", "0:v:0", "-c", "copy", str(recording),
    ], 42, output / "ffmpeg.log", wifi)
    playback = decode(recording, output / "preview.png")
    (output / "ffmpeg-playback.json").write_text(json.dumps(playback) + "\n", encoding="utf-8")
    if playback["seconds"] < 29:
        raise RuntimeError(f"FFmpeg recording decoded only {playback['seconds']:.2f}s")
    return {"ffmpeg": playback}


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
    playback = record_ffmpeg(ffmpeg, endpoint, output, recording, wifi)

    playback["vlc"] = record_vlc(vlc, endpoint, recording.with_name(recording.stem + "-vlc.mp4"),
                                 output, wifi, vlc_seconds, show_vlc)
    return playback


def record_vlc(vlc, endpoint, recording, output, wifi, seconds, show_video=False):
    vlc_command = [
        sys.executable, str(REPO / "tools/analysis/m3_vlc_smoke.py"), "--vlc", str(vlc),
        "--record-url", endpoint, "--record-seconds", str(seconds),
    ]
    if show_video:
        vlc_command.append("--show-video")
    vlc_log = client_command(
        vlc_command + [str(recording)], seconds + 15, output / "vlc.log", wifi,
    )
    vlc_playback = decode(recording, output / "vlc-preview.png")
    for line in vlc_log.splitlines():
        if line.startswith("HCV_VLC_VERSION "):
            vlc_playback["version"] = line.removeprefix("HCV_VLC_VERSION ")
    (output / "vlc-playback.json").write_text(json.dumps(vlc_playback) + "\n", encoding="utf-8")
    return vlc_playback


def compare_clients(vlc, ffmpeg, endpoint, output, recording, wifi, *,
                    transports=("tcp", "udp"), vlc_seconds=30, show_vlc=False):
    status = {}
    for transport in transports:
        destination = output / transport
        destination.mkdir()
        clip = recording.with_name(recording.stem + "-" + transport + ".mp4")
        try:
            status[transport] = record_ffmpeg(
                ffmpeg, endpoint, destination, clip, wifi, log_level="trace", transport=transport,
            )["ffmpeg"]
        except RuntimeError as error:
            status[transport] = {"error": redact(str(error), wifi)}

    destination = output / "vlc"
    destination.mkdir()
    try:
        status["vlc"] = record_vlc(
            vlc, endpoint, recording.with_name(recording.stem + "-vlc.mp4"),
            destination, wifi, vlc_seconds, show_vlc,
        )
        if status["vlc"]["seconds"] < vlc_seconds - 1.5:
            status["vlc"]["error"] = "VLC recording ended early"
    except RuntimeError as error:
        status["vlc"] = {"error": redact(str(error), wifi)}
    (output / "comparison.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    return status


def record_ap(vlc, ffmpeg, endpoint, address, port, output, recording, wifi, show_vlc):
    deadline = time.monotonic() + 44
    while True:
        try:
            with socket.create_connection((address, port), timeout=1):
                break
        except OSError:
            if time.monotonic() >= deadline:
                raise RuntimeError("K230 AP RTSP port was not reachable within 45 seconds") from None
            time.sleep(0.8)
    print("HCV_AP_RTSP_PORT_REACHABLE", flush=True)
    return compare_clients(vlc, ffmpeg, endpoint, output, recording, wifi,
                           transports=("tcp",), vlc_seconds=15, show_vlc=show_vlc)


def run(vlc, ffmpeg, ffprobe, port, show_vlc, single_ffmpeg, copy_packets, compare, ap, config):
    wifi = json.loads(config.read_text(encoding="utf-8"))["wifi"]
    network = "ap" if ap else "sta"
    if wifi["mode"] != network:
        raise ValueError(f"Host config must use wifi.mode={network}")
    name = ("m3-ap-" if ap else "m3-hotspot-") + datetime.now().strftime("%Y%m%d-%H%M%S")
    output = REPO / "station/logs" / name
    output.mkdir(parents=True)
    recording = REPO / "station/recordings" / (name + ".mp4")
    recording.parent.mkdir(parents=True, exist_ok=True)
    board = [sys.executable, "tools/board/board.py", "--port", port]
    entry = REPO / f"tools/board/m3_ch0_{network}_configurable_probe.py"
    if copy_packets or compare or ap:
        configured = output / "board-entry.py"
        options = b"HCV_COPY_PACKETS = True\n" if copy_packets else b"HCV_STREAM_SECONDS = 120\n"
        configured.write_bytes(options + entry.read_bytes())
        entry = configured
    command = board + ["run", str(entry), "--stream", "--timeout", "148" if compare or ap else "88"]
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
                    emit("HCV_RTSP_ENDPOINT_AVAILABLE")
                    continue
                emit(line)
                if line == "HCV_CH0_CONFIG_READY" and endpoint and recording_task is None:
                    emit("HCV_READY_HOST_CLOCK," + datetime.now().astimezone().isoformat(
                        timespec="milliseconds"
                    ))
                    if ap:
                        recording_task = worker.submit(
                            record_ap, vlc, ffmpeg, endpoint, address, int(port_text),
                            output, recording, wifi, show_vlc,
                        )
                    elif compare:
                        recording_task = worker.submit(
                            compare_clients, vlc, ffmpeg, endpoint, output, recording, wifi,
                        )
                    elif single_ffmpeg:
                        recording_task = worker.submit(
                            record_ffmpeg, ffmpeg, endpoint, output, recording, wifi,
                            log_level="debug",
                        )
                    else:
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
        if compare or ap:
            return code or int(any("error" in result for result in playback.values()))
        print("Recording:", recording)
        return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vlc", type=Path)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--ffprobe", type=Path)
    parser.add_argument("--port", default="auto")
    parser.add_argument("--show-vlc", action="store_true", help="show VLC video during the test")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--single-ffmpeg", action="store_true", help="use one FFmpeg client with debug logs")
    mode.add_argument("--compare-transports", action="store_true", help="compare TCP, UDP and VLC")
    mode.add_argument("--access-point", action="store_true", help="test the K230 AP with FFmpeg and VLC")
    parser.add_argument("--config", type=Path, default=REPO / "k230/config.local.json")
    parser.add_argument("--copy-packets", action="store_true", help="test the RTSP bytes interface")
    parser.add_argument("--localhost-mediamtx", type=Path, help="use a synthetic source instead of K230")
    parser.add_argument("--output", type=Path, help="new output directory for the synthetic test")
    args = parser.parse_args()
    if args.copy_packets and (not args.single_ffmpeg or args.localhost_mediamtx):
        parser.error("--copy-packets requires the single FFmpeg board test")
    if args.access_point and args.localhost_mediamtx:
        parser.error("--access-point requires the K230 board")
    ffmpeg = args.ffmpeg.resolve()
    vlc = ffprobe = None
    if not args.single_ffmpeg:
        if args.vlc is None or (
            args.ffprobe is None and not (args.compare_transports or args.access_point)
        ):
            parser.error("provide --vlc and --ffprobe, or select --single-ffmpeg")
        vlc = args.vlc.resolve()
        ffprobe = args.ffprobe.resolve() if args.ffprobe else None
    for executable in (ffmpeg, vlc, ffprobe):
        if executable is not None and not executable.is_file():
            parser.error(f"executable does not exist: {executable}")
    if args.localhost_mediamtx:
        if args.output is None:
            parser.error("--localhost-mediamtx requires --output")
        output = args.output.resolve()
        output.mkdir(parents=True)
        transports = ("tcp", "udp") if args.compare_transports else ("tcp",)
        with localhost_source(ffmpeg, args.localhost_mediamtx.resolve(), output, transports) as endpoint:
            wifi = {"ssid": "", "password": ""}
            if args.compare_transports:
                playback = compare_clients(vlc, ffmpeg, endpoint, output, output / "received.mp4", wifi)
            elif args.single_ffmpeg:
                playback = record_ffmpeg(
                    ffmpeg, endpoint, output, output / "received.mp4", wifi, log_level="debug",
                )
            else:
                port = endpoint.rsplit(":", 1)[1].split("/", 1)[0]
                playback = check_clients(vlc, ffmpeg, ffprobe, endpoint, "127.0.0.1", port,
                                         output, output / "received.mp4", wifi, vlc_seconds=30)
        print(json.dumps(playback))
        if args.compare_transports and any("error" in result for result in playback.values()):
            raise SystemExit(1)
    else:
        raise SystemExit(run(vlc, ffmpeg, ffprobe, args.port, args.show_vlc,
                             args.single_ffmpeg, args.copy_packets, args.compare_transports,
                             args.access_point, args.config))
