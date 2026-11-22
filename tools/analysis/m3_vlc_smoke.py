"""Exercise the VLC recording command with a synthetic localhost RTSP source."""

import argparse
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from station.record_rtsp import verify_recording, vlc_command


def local_port():
    with socket.socket() as tcp:
        tcp.bind(("127.0.0.1", 0))
        return tcp.getsockname()[1]


def stop(child):
    if child.poll() is None:
        child.terminate()
        child.wait(timeout=5)


def record_with_libvlc(vlc, url, recording, *, seconds=5, diagnostic_log=True):
    """Use the CLI's demux and sout options in a headless VLC 3 player."""
    argv = vlc_command(str(vlc), url, recording)
    options = [
        b"--ignore-config", b"--no-media-library", b"--quiet",
        b"--vout=dummy", b"--avcodec-hw=none", b"--rtsp-tcp", b"--network-caching=200",
    ]
    if diagnostic_log:
        options.remove(b"--quiet")
        options.extend([
            b"--verbose=2", b"--file-logging",
            ("--logfile=" + str(recording.parent / "client.log")).encode("utf-8"),
        ])
    dll_dir = os.add_dll_directory(str(vlc.parent))
    try:
        library = ctypes.CDLL(str(vlc.with_name("libvlc.dll")))
        instance_type = ctypes.c_void_p

        def api(name, restype, *argtypes):
            function = getattr(library, "libvlc_" + name)
            function.restype = restype
            function.argtypes = argtypes
            return function

        version = api("get_version", ctypes.c_char_p)().decode()
        create = api("new", instance_type, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p))
        args = (ctypes.c_char_p * len(options))(*options)
        instance = create(len(options), args)
        if not instance:
            raise RuntimeError("could not start LibVLC")
        with ExitStack() as resources:
            resources.callback(api("release", None, instance_type), instance)
            media = api("media_new_location", instance_type, instance_type, ctypes.c_char_p)(
                instance, url.encode("ascii")
            )
            if not media:
                raise RuntimeError("could not open RTSP in LibVLC")
            resources.callback(api("media_release", None, instance_type), media)
            media_option = api(
                "media_add_option_flag", None, instance_type, ctypes.c_char_p, ctypes.c_uint
            )
            media_option(media, (":sout=" + argv[argv.index("--sout") + 1]).encode("utf-8"), 2)
            media_option(media, b":avcodec-hw=none", 2)
            player = api("media_player_new_from_media", instance_type, instance_type)(media)
            if not player:
                raise RuntimeError("could not start LibVLC player")
            resources.callback(api("media_player_release", None, instance_type), player)
            if api("media_player_play", ctypes.c_int, instance_type)(player):
                raise RuntimeError("LibVLC could not start playback")
            resources.callback(api("media_player_stop", None, instance_type), player)
            state = api("media_player_get_state", ctypes.c_int, instance_type)
            deadline = time.monotonic() + 8
            while state(player) != 3:
                if time.monotonic() >= deadline or state(player) in (6, 7):
                    raise RuntimeError("LibVLC did not enter playback")
                time.sleep(0.1)
            time.sleep(seconds)
    finally:
        dll_dir.close()
    return version


def smoke(vlc, ffmpeg, mediamtx, output):
    output.mkdir()
    tcp = local_port()
    url = f"rtsp://127.0.0.1:{tcp}/hcv"
    config = output / "mediamtx.yml"
    config.write_text(
        f"rtspAddress: 127.0.0.1:{tcp}\nrtspTransports: [tcp]\n"
        "rtmp: false\nhls: false\nwebrtc: false\nsrt: false\nmoq: false\n"
        "paths:\n  hcv:\n    source: publisher\n", encoding="ascii"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    recording = output / "received sample.mp4"
    server_log = output / "server.log"
    with ExitStack() as stack:
        stream = stack.enter_context(server_log.open("w", encoding="utf-8"))
        server = subprocess.Popen(
            [str(mediamtx), str(config)], cwd=output, stdout=stream, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        stack.callback(stop, server)
        deadline = time.monotonic() + 5
        while "started with listeners" not in server_log.read_text(errors="replace"):
            if time.monotonic() >= deadline or server.poll() is not None:
                raise RuntimeError(f"MediaMTX did not start. See {server_log}")
            time.sleep(0.1)
        publisher_log = stack.enter_context((output / "publisher.log").open("wb"))
        publisher = subprocess.Popen([
            str(ffmpeg), "-nostdin", "-hide_banner", "-re", "-f", "lavfi", "-i",
            "testsrc2=size=320x192:rate=30", "-an", "-c:v", "libx264", "-tune", "zerolatency",
            "-g", "15", "-pix_fmt", "yuv420p", "-f", "rtsp", "-rtsp_transport", "tcp", url,
        ], stdout=publisher_log, stderr=subprocess.STDOUT, creationflags=flags)
        stack.callback(stop, publisher)
        deadline = time.monotonic() + 10
        while "stream is available and online" not in server_log.read_text(errors="replace"):
            if time.monotonic() >= deadline or server.poll() is not None or publisher.poll() is not None:
                raise RuntimeError(f"MediaMTX never saw the localhost video. See {server_log}")
            time.sleep(0.15)
        version = record_with_libvlc(vlc, url, recording)
    verify_recording(recording)
    video = cv2.VideoCapture(str(recording))
    try:
        metadata = {
            "vlc_version": version,
            "width": int(video.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(video.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": video.get(cv2.CAP_PROP_FPS),
            "output_bytes": recording.stat().st_size,
        }
        frames = 0
        while video.read()[0]:
            frames += 1
        metadata["decoded_frames"] = frames
    finally:
        video.release()
    (output / "result.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata))
    if frames < 60:
        raise RuntimeError("the five-second test recording contains fewer than two seconds of frames")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vlc", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--mediamtx", type=Path, required=True)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    smoke(args.vlc.resolve(), args.ffmpeg.resolve(), args.mediamtx.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
