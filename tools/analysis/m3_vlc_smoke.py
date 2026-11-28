"""Exercise the VLC recording command with a synthetic localhost RTSP source."""

import argparse
import ctypes
import json
import os
import socket
import subprocess
import sys
from threading import Thread
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

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


def record_with_libvlc(vlc, url, recording, *, seconds=5, diagnostic_log=True, show_video=False,
                       transport="tcp", cache_ms=200, observation=None, window=None, on_playing=None,
                       hubei=False, on_rendered_frame=None):
    """Use the CLI's demux and recording options in a VLC 3 player."""
    argv = vlc_command(str(vlc), url, recording, display=show_video or hubei)
    options = [
        b"--ignore-config", b"--no-media-library", b"--verbose=2",
        b"--avcodec-hw=none", f"--network-caching={cache_ms}".encode("ascii"),
    ]
    options.append(b"--rtsp-tcp" if transport == "tcp" else b"--no-rtsp-tcp")
    render_pipe = None
    if hubei:
        if show_video:
            options.remove(b"--avcodec-hw=none")
        options.extend([b"--no-one-instance", b"--live-caching=200", b"--no-audio"])
        if not show_video:
            from station.bench.render_pipe import RenderPipe
            render_pipe = RenderPipe(640, 360, on_rendered_frame)
            options.extend([b"--vout=yuv", b"--yuv-chroma=I420", ("--yuv-file=" + render_pipe.name).encode()])
    elif show_video:
        options.append(b"--vout=wingdi")
    if diagnostic_log:
        options.extend([
            b"--file-logging",
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
            if not hubei or not show_video:
                media_option(media, b":avcodec-hw=none", 2)
            player = api("media_player_new_from_media", instance_type, instance_type)(media)
            if not player:
                raise RuntimeError("could not start LibVLC player")
            resources.callback(api("media_player_release", None, instance_type), player)
            if window is not None:
                api("media_player_set_hwnd", None, instance_type, ctypes.c_void_p)(player, window.hwnd)
            if api("media_player_play", ctypes.c_int, instance_type)(player):
                raise RuntimeError("LibVLC could not start playback")
            resources.callback(api("media_player_stop", None, instance_type), player)
            state = api("media_player_get_state", ctypes.c_int, instance_type)
            deadline = time.monotonic() + 8
            while (current_state := state(player)) != 3:
                if time.monotonic() >= deadline or current_state in (6, 7):
                    raise RuntimeError(f"LibVLC did not enter playback (state={current_state})")
                if window is not None:
                    window.tick()
                time.sleep(0.02 if window is not None else 0.1)
            started = time.monotonic()
            display_epoch = time.time()
            if on_playing is not None:
                on_playing()
            elapsed = 0
            if hubei:
                first = last = previous = None
                samples = 0
                next_sample = longest = 0.0
                longest_start = 0.0
            while elapsed < seconds:
                if state(player) in (6, 7):
                    if observation is not None:
                        observation(elapsed, state(player))
                    break
                if window is not None:
                    window.tick(elapsed)
                if hubei and elapsed >= next_sample:
                    next_sample = elapsed + 0.2
                    if window is None:
                        if render_pipe.error:
                            raise render_pipe.error
                        current = render_pipe.frames.picture
                    else:
                        # Sample the returned image only. The running PC clock is on the left.
                        image = window.grab()
                        w, h = image.size
                        current = image.crop((w // 2, h // 6, w, 5 * h // 6)).resize((160, 96)).tobytes()
                    if current is not None and current != previous:
                        if last is None or elapsed - last > 1:
                            first = elapsed
                        previous, last = current, elapsed
                        samples += 1
                        if last - first > longest:
                            longest = last - first
                            longest_start = first
                time.sleep(min(0.01 if window is not None else 0.1, seconds - elapsed))
                elapsed = time.monotonic() - started
            if hubei:
                display = dict(changed_display_samples=samples,
                      continuous_display_s=round(longest, 3),
                      display_start_epoch=display_epoch + longest_start,
                      display_end_epoch=display_epoch + longest_start + longest)
                if render_pipe is not None:
                    display["headless_rendered_frames"] = render_pipe.frames.frame_count
                print("HCV_DISPLAY " + json.dumps(display), flush=True)
    finally:
        dll_dir.close()
        if render_pipe is not None:
            render_pipe.close()
    return version


def logged_child(stack, command, output, redact, **options):
    log = stack.enter_context(output.open("w", encoding="utf-8"))
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", **options)

    def collect():
        with child.stdout:
            for line in child.stdout:
                log.write(redact(line))
                log.flush()

    worker = Thread(target=collect)
    worker.start()
    stack.callback(worker.join)
    stack.callback(stop, child)
    return child


@contextmanager
def localhost_source(ffmpeg, mediamtx, output, transports=("tcp",), *,
                     size="320x192", fps=30, gop=15, bitrate_kbps=None, source_seconds=None,
                     redact=lambda line: line):
    tcp = local_port()
    url = f"rtsp://127.0.0.1:{tcp}/hcv"
    config = output / "mediamtx.yml"
    config.write_text(
        f"rtspAddress: 127.0.0.1:{tcp}\nrtspTransports: [{', '.join(transports)}]\n"
        "rtmp: false\nhls: false\nwebrtc: false\nsrt: false\nmoq: false\n"
        "paths:\n  hcv:\n    source: publisher\n", encoding="ascii"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    server_log = output / "server.log"
    with ExitStack() as stack:
        server = logged_child(
            stack, [str(mediamtx), str(config)], server_log, redact, cwd=output,
            creationflags=flags,
        )
        deadline = time.monotonic() + 5
        while "started with listeners" not in server_log.read_text(errors="replace"):
            if time.monotonic() >= deadline or server.poll() is not None:
                raise RuntimeError(f"MediaMTX did not start. See {server_log}")
            time.sleep(0.1)
        publisher = logged_child(stack, [
            str(ffmpeg), "-nostdin", "-hide_banner", "-re", "-f", "lavfi", "-i",
            f"testsrc2=size={size}:rate={fps}", "-an", "-c:v", "libx264", "-tune", "zerolatency",
            "-g", str(gop), *(["-b:v", f"{bitrate_kbps}k"] if bitrate_kbps else []),
            *(["-t", str(source_seconds)] if source_seconds else []),
            "-pix_fmt", "yuv420p", "-f", "rtsp", "-rtsp_transport", "tcp", url,
        ], output / "publisher.log", redact, creationflags=flags)
        deadline = time.monotonic() + 10
        while "stream is available and online" not in server_log.read_text(errors="replace"):
            if time.monotonic() >= deadline or server.poll() is not None or publisher.poll() is not None:
                raise RuntimeError(f"MediaMTX never saw the localhost video. See {server_log}")
            time.sleep(0.15)
        yield url


def smoke(vlc, ffmpeg, mediamtx, output):
    import cv2

    output.mkdir()
    recording = output / "received sample.mp4"
    with localhost_source(ffmpeg, mediamtx, output) as url:
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
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--mediamtx", type=Path)
    parser.add_argument("--record-url", help="record an existing source; send native logs to stderr")
    parser.add_argument("--record-seconds", type=int, default=5)
    parser.add_argument("--show-video", action="store_true")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.record_url:
        version = record_with_libvlc(
            args.vlc.resolve(), args.record_url, args.output.resolve(),
            seconds=args.record_seconds, diagnostic_log=False, show_video=args.show_video,
        )
        print(f"HCV_VLC_VERSION {version}", flush=True)
        return
    if args.ffmpeg is None or args.mediamtx is None:
        parser.error("synthetic mode requires --ffmpeg and --mediamtx")
    smoke(args.vlc.resolve(), args.ffmpeg.resolve(), args.mediamtx.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
