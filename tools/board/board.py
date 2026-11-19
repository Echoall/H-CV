"""Host-side USB serial tools for the H-CV K230."""

import argparse
from contextlib import ExitStack
import json
from math import isfinite
from pathlib import Path, PurePosixPath
import sys
import time

from mpremote.transport import TransportError
from mpremote.transport_serial import SerialTransport
from serial.tools import list_ports


REPO = Path(__file__).resolve().parents[2]
BACKUP = REPO / "reports" / "data" / "P0-EXP00" / "board-backup"
SDCARD = "/sdcard"


def detect_ports():
    for port in list_ports.comports():
        print(port.device, port.description, port.hwid, sep="\t")


def backup_sdcard(board, directory):
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "sdcard.json").is_file():
        return

    entries = []
    pending = [SDCARD]
    while pending:
        parent = pending.pop()
        for item in sorted(board.fs_listdir(parent), key=lambda entry: entry.name):
            if item.name in (".", ".."):
                continue
            path = parent + "/" + item.name
            is_directory = bool(item.st_mode & 0x4000)
            entries.append({"path": path, "directory": is_directory, "bytes": item.st_size})
            if is_directory:
                pending.append(path)

    startup = SDCARD + "/main.py"
    if any(entry["path"] == startup and not entry["directory"] for entry in entries):
        (directory / "main.py").write_bytes(board.fs_readfile(startup))
    listing = directory / "sdcard.json"
    temporary = listing.with_suffix(".tmp")
    temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(listing)


def project_path(path):
    parsed = PurePosixPath(path)
    if (not parsed.is_relative_to("/sdcard/hcv") or parsed == PurePosixPath("/sdcard/hcv")
            or ".." in parsed.parts):
        raise ValueError("board writes must target a file inside /sdcard/hcv")
    return parsed


def upload(board, local, remote):
    destination = project_path(remote)
    content = local.read_bytes()
    for parent in reversed(destination.parents):
        if parent.is_relative_to("/sdcard/hcv") and not board.fs_isdir(str(parent)):
            board.fs_mkdir(str(parent))
    board.fs_writefile(str(destination), content)


def run_script(board, source, timeout, emit):
    """Execute source and read the two raw REPL output streams."""
    deadline = time.monotonic() + timeout
    at_prompt = False

    def read_stream(consumer=None):
        pending = bytearray()

        def consume(byte):
            if time.monotonic() >= deadline:
                raise TimeoutError("board script timed out")
            if byte != b"\x04":
                if consumer is None:
                    pending.extend(byte)
                else:
                    consumer(byte)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("board script timed out")
        end = board.read_until(1, b"\x04", timeout=None, timeout_overall=remaining,
                               data_consumer=consume)
        if end != b"\x04":
            raise TimeoutError("board script timed out")
        return pending

    try:
        board.exec_raw_no_follow(source)
        read_stream(emit)
        errors = read_stream()
        at_prompt = True
    finally:
        if not at_prompt:
            board.serial.write(b"\x03\x03")
            try:
                board.exit_raw_repl()
                board.enter_raw_repl(soft_reset=False, timeout_overall=5)
            except (OSError, TransportError):
                print("Could not recover raw REPL. Reconnect the board before retrying.",
                      file=sys.stderr)
    if errors:
        emit(errors)
        raise TransportError("script raised a board-side exception")


def run_command(board, args):
    with ExitStack() as stack:
        log = stack.enter_context(args.log.open("wb")) if args.log else None
        pending = bytearray()

        def emit(data):
            pending.extend(data)
            if len(pending) >= 4096 or (args.stream and b"\n" in data):
                flush()

        def flush():
            if not pending:
                return
            sys.stdout.buffer.write(pending)
            sys.stdout.buffer.flush()
            if log:
                log.write(pending)
                log.flush()
            pending.clear()

        try:
            run_script(board, args.script.read_bytes(), args.timeout, emit)
        finally:
            flush()


def positive_timeout(text):
    value = float(text)
    if not isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number")
    return value


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="USB serial port, e.g. COM3")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("ports", "info", "backup", "reset"):
        commands.add_parser(command)
    listing = commands.add_parser("ls")
    listing.add_argument("remote", nargs="?", default=SDCARD)
    put = commands.add_parser("put")
    put.add_argument("local", type=Path)
    put.add_argument("remote")
    get = commands.add_parser("get")
    get.add_argument("remote")
    get.add_argument("local", type=Path)
    run = commands.add_parser("run")
    run.add_argument("script", type=Path)
    run.add_argument("--timeout", type=positive_timeout, default=30.0)
    run.add_argument("--stream", action="store_true")
    run.add_argument("--log", type=Path)
    args = parser.parse_args()
    if args.command != "ports" and not args.port:
        parser.error("--port is required; use the ports command to list candidates")
    return args


def main():
    args = arguments()
    if args.command == "ports":
        detect_ports()
        return 0

    try:
        board = SerialTransport(args.port, timeout=1)
        try:
            board.serial.write_timeout = 2
            board.enter_raw_repl(soft_reset=False)
            if args.command == "info":
                print(board.exec("import os, sys\nprint(os.uname())\nprint(sys.implementation)")
                      .decode("utf-8", errors="replace").strip())
            elif args.command == "backup":
                backup_sdcard(board, BACKUP)
                print(BACKUP)
            elif args.command == "ls":
                for item in board.fs_listdir(args.remote):
                    print(item.name)
            elif args.command == "get":
                args.local.write_bytes(board.fs_readfile(args.remote))
            elif args.command == "put":
                project_path(args.remote)
                backup_sdcard(board, BACKUP)
                upload(board, args.local, args.remote)
            elif args.command == "run":
                run_command(board, args)
            elif args.command == "reset":
                board.exit_raw_repl()
                board.serial.write(b"\x04")
        finally:
            try:
                if board.in_raw_repl:
                    board.exit_raw_repl()
            finally:
                board.close()
    except (OSError, ValueError, TransportError) as error:
        print(f"board: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
