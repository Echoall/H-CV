"""Receive one H.264 RTSP/TCP session and copy its video to disk and a local player."""
from contextlib import ExitStack, contextmanager
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlsplit

from station.bench.rtp_trace import trace


def commands(ffmpeg, ffplay, url, framedrop):
    ingest = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-n", "-rtsp_transport", "tcp",
              "-probesize", "32768", "-analyzeduration", "1", "-i", url]
    copy = ["-map", "0:v:0", "-c:v", "copy", "-f", "mpegts", "-muxdelay", "0", "-muxpreload", "0"]
    display = [ffplay, "-hide_banner", "-nostats", "-loglevel", "warning", "-autoexit",
               "-fflags", "nobuffer", "-flags", "low_delay", "-framedrop" if framedrop else "-noframedrop",
               "-probesize", "32768", "-analyzeduration", "1", "-an", "-f", "mpegts", "-i", "pipe:0"]
    return ingest + copy + ["recording.ts"] + copy + ["-flush_packets", "1", "pipe:1"], display


def stop(child):
    if child.poll() is None:
        child.kill()
        child.wait(timeout=5)


def continuity(rows):
    times = [r[0] for r in rows]
    if len(times) < 2:
        return dict(frames=len(times), max_gap_ms=None, longest_s=0, continuous_60s=False)
    start = times[0]
    longest = largest = 0
    for prev, now in zip(times, times[1:]):
        if now < prev:
            raise ValueError("host timestamps moved backwards")
        gap = now - prev
        largest = max(largest, gap)
        if gap > 1_000_000_000:
            start = now
        longest = max(longest, now - start)
    return dict(frames=len(times), max_gap_ms=round(largest / 1e6, 3), longest_s=round(longest / 1e9, 3),
                continuous_60s=longest >= 60_000_000_000)


@contextmanager
def receiver_path(url, directory, diagnostic, result):
    result["receiver_path"] = "rtp_relay" if diagnostic else "direct"
    if not diagnostic:
        yield url
        return
    rows, stats = [], {}
    try:
        with trace(url, stats, lambda _: "[omitted]", on_frame_end=lambda *row: rows.append(row)) as local:
            yield local
    finally:
        (directory / "arrival.json").write_text(json.dumps(rows), encoding="utf-8")
        result.update(arrival=continuity(rows), rtp_discontinuities=stats.get("rtp_sequence_discontinuities", 0),
                      inspection_stopped=bool(stats.get("rtp_inspection_stopped")))


def remux_mp4(directory, ffmpeg):
    source = directory / "recording.ts"
    if not source.is_file() or not source.stat().st_size:
        return False
    temporary = directory / "recording.partial.mp4"
    try:
        with (directory / "remux.log").open("wb") as log:
            command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                       "-i", "recording.ts", "-map", "0:v:0", "-c", "copy", "-movflags", "+faststart",
                       "-n", temporary.name]
            process = subprocess.run(command, cwd=directory, stdout=log, stderr=log)
        if process.returncode != 0 or not temporary.is_file() or not temporary.stat().st_size:
            return False
        temporary.replace(directory / "recording.mp4")
        return True
    finally:
        temporary.unlink(missing_ok=True)


def receive(url, directory, ffmpeg, ffplay, seconds, framedrop, *, diagnostic=False, env=None, tick=None):
    target = urlsplit(url)
    if (target.scheme != "rtsp" or not target.hostname or not target.port or target.username is not None
            or target.path in ("", "/") or target.query or target.fragment or seconds <= 0):
        raise ValueError("请输入板子给出的 RTSP 地址（含端口和路径），时长须大于零。")
    directory.mkdir(parents=True)
    result = {}
    reason = "deadline"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with receiver_path(url, directory, diagnostic, result) as local:
        ingest, display = commands(ffmpeg, ffplay, local, framedrop)
        with ExitStack() as stack:
            log = stack.enter_context((directory / "receiver.log").open("wb"))
            plog = stack.enter_context((directory / "player.log").open("wb"))
            writer = subprocess.Popen(ingest, cwd=directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=log, creationflags=flags)
            stack.callback(writer.stdin.close)
            stack.callback(writer.stdout.close)
            stack.callback(stop, writer)
            viewer = subprocess.Popen(display, cwd=directory, env=env, stdin=writer.stdout,
                                      stdout=plog, stderr=plog, creationflags=flags)
            stack.callback(stop, viewer)
            writer.stdout.close()
            deadline = time.monotonic() + seconds
            try:
                while time.monotonic() < deadline:
                    if writer.poll() is not None or viewer.poll() is not None:
                        reason = "receiver_exit" if writer.poll() is not None else "player_exit"
                        break
                    if tick:
                        tick()
                    time.sleep(0.006 if tick else 0.05)
            except KeyboardInterrupt:
                reason = "cancelled"
            finally:
                if writer.poll() is None:
                    try:
                        writer.stdin.write(b"q\n")
                        writer.stdin.flush()
                        writer.wait(timeout=5)
                    except (BrokenPipeError, subprocess.TimeoutExpired):
                        stop(writer)
                try:
                    viewer.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    stop(viewer)
                if tick:
                    tick()
    result.update(reason=reason, recorder_exit=writer.returncode, player_exit=viewer.returncode,
                  framedrop=framedrop, mp4_ready=remux_mp4(directory, ffmpeg))
    (directory / "receiver.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def binaries():
    runtime = Path(__file__).parent / "runtime"
    paths = []
    for name in ("ffmpeg", "ffplay"):
        exe = runtime / (name + ".exe")
        path = exe if exe.is_file() else shutil.which(name)
        if not path:
            raise RuntimeError(f"电脑缺少 {name}。可放在调试台 runtime 目录。")
        paths.append(path)
    return paths


def receiver_cli(url, runs, seconds, framedrop, *, diagnostic=False):
    directory = runs.resolve() / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-low-delay")
    result = receive(url, directory, *binaries(), seconds, framedrop, diagnostic=diagnostic)
    print("接收结果", result, "\n录像与日志", directory)
    return int(result["reason"] != "deadline" or result["recorder_exit"] != 0
               or result["player_exit"] != 0 or not result["mp4_ready"]
               or (diagnostic and not result["arrival"]["frames"]))
