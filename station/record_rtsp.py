"""Display and record one K230 RTSP test with VLC."""

import argparse
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


def find_vlc():
    executable = shutil.which("vlc")
    if executable:
        return executable
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(variable):
            candidate = Path(os.environ[variable]) / "VideoLAN" / "VLC" / "vlc.exe"
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError("VLC was not found. Pass --vlc with the path to vlc.exe.")


def vlc_command(executable, url, output, *, display=True):
    source = urlsplit(url)
    if source.scheme != "rtsp" or not source.hostname:
        raise ValueError("source must be an RTSP URL printed by the K230")
    path = output.resolve().as_posix()
    if any(character in path for character in ('"', "\\", "\r", "\n")):
        raise ValueError("recording path cannot be quoted for VLC")
    file_output = 'std{access=file,mux=mp4,dst="' + path + '"}'
    sout = "#duplicate{dst=display,dst=" + file_output + "}" if display else "#" + file_output
    return [
        executable, "--no-one-instance", "--rtsp-tcp", "--network-caching=200",
        "--sout", sout, "--play-and-exit", url,
    ]


def verify_recording(recording):
    import cv2

    if not recording.is_file() or not recording.stat().st_size:
        raise RuntimeError("VLC did not save a recording")
    video = cv2.VideoCapture(str(recording))
    try:
        decoded, _ = video.read()
        if not decoded:
            raise RuntimeError(f"VLC saved {recording}, but no video frame could be decoded")
    finally:
        video.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="rtsp://<board-ip>:8554/hcv")
    parser.add_argument("--test", type=int, required=True, help="test number for the filename")
    parser.add_argument("--output", type=Path, default=Path("recordings"))
    parser.add_argument("--vlc", help="path to VLC if it is not on PATH")
    args = parser.parse_args()
    if args.test < 0:
        parser.error("test number must be nonnegative")
    args.output.mkdir(parents=True, exist_ok=True)
    filename = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_T{args.test:02d}.mp4"
    recording = args.output / filename
    if recording.exists():
        raise FileExistsError(recording)
    command = vlc_command(args.vlc or find_vlc(), args.url, recording)
    print(f"关闭本次 VLC 窗口结束录像。保存到 {recording.resolve()}", flush=True)
    subprocess.run(command, check=True)
    verify_recording(recording)
    print(recording.resolve())


if __name__ == "__main__":
    main()
