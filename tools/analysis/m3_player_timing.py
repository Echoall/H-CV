"""Compare host-only synthetic video age at two headless player outputs on Windows."""
import argparse
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import subprocess
from threading import Event, Thread
import time

import numpy as np
from PIL import Image

from tools.analysis.m3_vlc_smoke import local_port, record_with_libvlc, stop


WIDTH, HEIGHT, FPS = 640, 360, 60
COLS, ROWS = 76, 42


def marker(frame, sequence):
    code = sequence % (COLS * ROWS)
    x, y = 8 + 8 * (code % COLS), 8 + 8 * (code // COLS)
    frame[y:y + 8, x:x + 8] = 240


def identify(frame):
    # Sample the center of each possible marker. The patterned background stays below 128.
    rows, cols = np.where(frame[12:12 + ROWS * 8:8, 12:12 + COLS * 8:8] > 180)
    if len(rows) == 1:
        return int(rows[0]) * COLS + int(cols[0])
    return None


def publish(writer, stamps, done):
    y, x = np.indices((HEIGHT, WIDTH), dtype=np.int32)
    background = ((x // 8 + y // 7) % 64).astype(np.uint8)
    deadline = time.monotonic()
    index = 0
    while not done.is_set():
        frame = background + (index % 48)
        marker(frame, index)
        stamps[index % (COLS * ROWS)] = (index, time.time_ns())
        writer.write(frame)
        index += 1
        deadline = max(deadline + 1 / FPS, time.monotonic())
        done.wait(max(0, deadline - time.monotonic()))


def collect_bitmaps(directory, observe):
    for path in sorted(directory.glob("SDL_window*-*.bmp")):
        try:
            with Image.open(path) as image:
                frame = np.asarray(image.convert("L"))
            stamp = path.stat().st_mtime_ns
        except (OSError, ValueError):
            # The SDL writer may still have the current bitmap open.
            continue
        if frame.shape == (HEIGHT, WIDTH):
            observe(frame, stamp)
        path.unlink()


def measure(player, url, directory, stamps, seconds, cache=None):
    directory.mkdir()
    samples = []
    rejected = 0

    def observe(frame, observed):
        nonlocal rejected
        code = identify(frame)
        if code not in stamps:
            rejected += 1
            return
        index, sent = stamps[code]
        samples.append((index, sent, observed))

    started = time.time_ns()
    if cache is not None:
        def rendered(data):
            stamp = time.time_ns()
            pixels = np.frombuffer(data, dtype=np.uint8, count=WIDTH * HEIGHT).reshape(HEIGHT, WIDTH)
            observe(pixels, stamp)

        record_with_libvlc(player, url, directory / "recording.mp4", diagnostic_log=False, hubei=True, seconds=seconds,
                           cache_ms=cache, on_rendered_frame=rendered)
        return summarize(samples, started, rejected)

    with ExitStack() as stack:
        output = stack.enter_context((directory / "player.log").open("wb"))
        env = os.environ.copy()
        env.update(SDL_VIDEODRIVER="dummy", SDL_VIDEO_DRIVER="dummy", SDL_VIDEO_DUMMY_SAVE_FRAMES="1",
                   SDL_RENDER_DRIVER="software")
        cmd = [player, "-nostats", "-loglevel", "warning", "-rtsp_transport", "tcp", "-fflags",
               "nobuffer", "-flags", "low_delay", "-an", "-x", str(WIDTH), "-y", str(HEIGHT), url]
        child = subprocess.Popen(cmd, cwd=directory, env=env, stdout=output, stderr=output,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        stack.callback(stop, child)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(f"{directory.name} exited early ({child.returncode})")
            collect_bitmaps(directory, observe)
            time.sleep(0.006)
    collect_bitmaps(directory, observe)
    return summarize(samples, started, rejected)


def summarize(samples, started, rejected):
    if not samples:
        raise RuntimeError("No synthetic marker reached the player output")
    # Remove startup by elapsed output time so late-starting players have the same warmup.
    warm = samples[0][2] + 2_000_000_000
    unique = []
    for row in samples:
        if row[2] >= warm and (not unique or row[0] != unique[-1][0]):
            unique.append(row)
    if len(unique) < 2 or any(b[0] <= a[0] or b[2] < a[2] for a, b in zip(unique, unique[1:])):
        raise RuntimeError("Insufficient or out-of-order marker output")
    numbers, sent, seen = np.asarray(unique, dtype=np.int64).T
    age = (seen - sent) / 1e6
    if np.min(age) < 0:
        raise RuntimeError("Inconsistent host clock")
    return dict(first_frame_ms=round((samples[0][2] - started) / 1e6, 2), frames=len(age),
                rejected=rejected, missing=int(numbers[-1] - numbers[0] + 1 - len(age)),
                span_s=round(float(seen[-1] - seen[0]) / 1e9, 3),
                max_gap_ms=round(float(np.max(np.diff(seen))) / 1e6, 2),
                p50_age_ms=round(float(np.median(age)), 2), p95_age_ms=round(float(np.percentile(age, 95)), 2),
                rows=unique)


@contextmanager
def marked_source(out, ffmpeg, mediamtx):
    tcp = local_port()
    url = f"rtsp://127.0.0.1:{tcp}/hcv"
    cfg = out / "server.yml"
    cfg.write_text(f"rtspAddress: 127.0.0.1:{tcp}\nrtspTransports: [tcp]\n"
                   "rtmp: false\nhls: false\nwebrtc: false\nsrt: false\nmoq: false\npaths:\n  hcv: {}\n")
    stamps = {}
    with ExitStack() as stack:
        def spawn(name, command, **kw):
            log = stack.enter_context((out / (name + ".log")).open("wb"))
            child = subprocess.Popen(command, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW, **kw)
            stack.callback(stop, child)
            return child

        server = spawn("server", [mediamtx.resolve(), cfg], cwd=out)
        def wait_for(text):
            deadline = time.monotonic() + 12
            while text not in (out / "server.log").read_text(errors="replace"):
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("MediaMTX did not report " + text)
                time.sleep(0.05)

        wait_for("started with listeners")
        encoder = spawn("publisher", [ffmpeg.resolve(), "-hide_banner", "-loglevel", "warning",
                        "-f", "rawvideo", "-pixel_format", "gray", "-video_size", f"{WIDTH}x{HEIGHT}",
                        "-framerate", str(FPS), "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset",
                        "ultrafast", "-tune", "zerolatency", "-g", "10", "-pix_fmt", "yuv420p",
                        "-f", "rtsp", "-rtsp_transport", "tcp", url], stdin=subprocess.PIPE)
        done = Event()
        thread = Thread(target=publish, args=(encoder.stdin, stamps, done), daemon=True)
        thread.start()

        def stop_feeder():
            done.set()
            thread.join(2)
            if thread.is_alive():
                stop(encoder)
                thread.join(2)
            encoder.stdin.close()

        stack.callback(stop_feeder)
        wait_for("stream is available and online")
        yield url, stamps


def compare(args):
    out = args.output.resolve()
    out.mkdir()
    result = {}
    with marked_source(out, args.ffmpeg, args.mediamtx) as (url, stamps):
        settings = [None if value == "ffplay" else int(value) for value in args.players]
        for run in range(1, args.rounds + 1):
            order = settings if run % 2 else reversed(settings)
            for cache in order:
                name = f"round{run}-" + ("ffplay" if cache is None else f"vlc{cache}")
                player = args.ffplay if cache is None else args.vlc
                measurement = measure(player.resolve(), url, out / name, stamps, args.seconds, cache)
                rows = measurement.pop("rows")
                (out / (name + ".json")).write_text(json.dumps(rows))
                result[name] = measurement
                (out / "summary.json").write_text(json.dumps(result, indent=2))
                print(name, measurement, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "ffmpeg", "mediamtx", "vlc", "ffplay"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--players", nargs="+", choices=("100", "150", "200", "ffplay"),
                        default=["100", "150", "200", "ffplay"])
    parser.add_argument("--rounds", choices=(1, 2), type=int, default=2)
    compare(parser.parse_args())
