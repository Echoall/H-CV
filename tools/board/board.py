"""Host-side USB serial tools for the H-CV K230."""

import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path, PurePosixPath
import sys
import time

from mpremote.transport import TransportError
from mpremote.transport_serial import SerialTransport
from serial import serial_for_url
from serial.tools import list_ports


REPO = Path(__file__).resolve().parents[2]
BACKUP = REPO / "reports" / "data" / "P0-EXP00" / "board-backup"
SDCARD = "/sdcard"
CANMV_USB_ID = (0x1209, 0xABD1)
HASH_LIMIT = 1024 * 1024


class BoardRecoveryError(TransportError):
    """Manual power cycle required before board access can continue."""


def detect_ports():
    for port in list_ports.comports():
        print(port.device, port.description, port.hwid, sep="\t")



def select_port(requested):
    if requested != "auto":
        return requested
    candidates = [port.device for port in list_ports.comports()
                  if (port.vid, port.pid) == CANMV_USB_ID]
    if len(candidates) != 1:
        raise ValueError("expected one CanMV USB port, found " + str(len(candidates))
                         + "; use ports and pass --port explicitly")
    return candidates[0]


class ControlledSerialTransport(SerialTransport):
    """The pinned raw REPL transport with explicit initial line levels."""

    def __init__(self, device, dtr, rts):
        self.device_name = device
        self.in_raw_repl = False
        self.use_raw_paste = True
        self.mounted = False
        self.is_pty = self._is_pty_device(device)
        self.serial = serial_for_url(device, do_not_open=True, baudrate=115200,
                                     timeout=1, exclusive=True, write_timeout=2)
        if dtr != "default":
            self.serial.dtr = dtr == "1"
        if rts != "default":
            self.serial.rts = rts == "1"
        self.serial.open()


def require_backup(directory=BACKUP):
    if not (directory / "sdcard.json").is_file():
        raise FileNotFoundError("run the board.py backup command before put, run or verify")


def board_has_sha256(board):
    result = board.exec(
        "try:\n"
        "    import hashlib\n"
        "    hashlib.sha256()\n"
        "except (ImportError, AttributeError):\n"
        "    print('HCV_SHA256=0')\n"
        "else:\n"
        "    print('HCV_SHA256=1')\n"
    )
    return result.strip().endswith(b"HCV_SHA256=1")


def backup_sdcard(board, directory, timeout=600):
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "sdcard.json"
    if manifest.is_file():
        print("Using existing TF-card backup.", file=sys.stderr, flush=True)
        return

    deadline = time.monotonic() + timeout

    def check_time(*_progress):
        if time.monotonic() >= deadline:
            raise TimeoutError("TF-card backup exceeded its 10-minute budget")

    print("Reading TF-card directory and selected files...", file=sys.stderr, flush=True)
    device_hash = board_has_sha256(board)
    entries, pending = [], [SDCARD]
    file_count = total_bytes = hashed = skipped = 0
    while pending:
        check_time()
        parent = pending.pop()
        for item in sorted(board.fs_listdir(parent), key=lambda entry: entry.name):
            check_time()
            if item.name in (".", ".."):
                continue
            path = parent + "/" + item.name
            is_directory = bool(item.st_mode & 0x4000)
            entry = {"path": path, "directory": is_directory, "bytes": item.st_size}
            entries.append(entry)
            if is_directory:
                pending.append(path)
                continue

            file_count += 1
            total_bytes += item.st_size
            root_script = parent == SDCARD and item.name.lower().endswith(".py")
            hash_file = root_script or item.st_size <= HASH_LIMIT
            entry["hashed"] = hash_file
            if not hash_file:
                skipped += 1
                try:
                    entry["mtime"] = board.fs_stat(path).st_mtime
                except (AttributeError, OSError, TransportError):
                    pass
            elif root_script or not device_hash:
                content = board.fs_readfile(path, progress_callback=check_time)
                entry["sha256"] = sha256(content).hexdigest()
                hashed += 1
                if root_script:
                    if "/" in item.name or "\\" in item.name:
                        raise ValueError("invalid filename on the SD card")
                    (directory / item.name).write_bytes(content)
            else:
                entry["sha256"] = board.fs_hashfile(path, "sha256").hex()
                hashed += 1

            if file_count == 1 or file_count % 10 == 0:
                print(f"Backup: {file_count} files listed, {hashed} hashed, {skipped} skipped",
                      file=sys.stderr, flush=True)
    check_time()
    summary = {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "hashed_file_count": hashed,
        "unhashed_file_count": skipped,
        "entries": entries,
    }
    temporary = manifest.with_suffix(".tmp")
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(manifest)
    print(f"Backup complete: {file_count} files, {hashed} hashed, {skipped} skipped",
          file=sys.stderr, flush=True)


def project_path(path):
    parsed = PurePosixPath(path)
    if (not parsed.is_relative_to("/sdcard/hcv") or parsed == PurePosixPath("/sdcard/hcv")
            or ".." in parsed.parts or "\\" in path or "\x00" in path):
        raise ValueError("board writes must target a file inside /sdcard/hcv")
    return parsed


def upload(board, local, remote):
    destination = project_path(remote)
    content = local.read_bytes()
    for parent in reversed(destination.parents):
        if parent.is_relative_to("/sdcard/hcv") and not board.fs_isdir(str(parent)):
            board.fs_mkdir(str(parent))
    staged = destination.with_name("." + destination.name + ".hcv-upload")
    previous = destination.with_name("." + destination.name + ".hcv-previous")
    if board.fs_exists(str(previous)):
        if board.fs_exists(str(destination)):
            board.fs_rmfile(str(previous))
        else:
            rename(board, previous, destination)

    board.fs_writefile(str(staged), content, chunk_size=256)
    if board.fs_hashfile(str(staged), "sha256") != sha256(content).digest():
        raise ValueError("upload checksum mismatch; original file was kept")

    had_original = board.fs_exists(str(destination))
    if had_original:
        rename(board, destination, previous)
    try:
        rename(board, staged, destination)
    except TransportError:
        if had_original:
            rename(board, previous, destination)
        raise
    if had_original:
        board.fs_rmfile(str(previous))


def rename(board, source, destination):
    board.exec("import os\nos.rename({!r}, {!r})".format(str(source), str(destination)))


def cleanup_media(board):
    result = board.exec(
        "try:\n"
        "    from media.media import MediaManager\n"
        "except ImportError:\n"
        "    print('MediaManager unavailable')\n"
        "else:\n"
        "    if hasattr(MediaManager, 'deinit'):\n"
        "        MediaManager.deinit()\n"
        "        print('MediaManager.deinit completed')\n"
        "    else:\n"
        "        print('MediaManager.deinit unavailable')\n"
    )
    return result.decode("utf-8", errors="replace").strip()


def recover(board):
    for soft_reset, timeout in ((False, 3), (True, 10)):
        try:
            board.serial.write(b"\x03\x03")
            board.exit_raw_repl()
            board.enter_raw_repl(soft_reset=soft_reset, timeout_overall=timeout)
        except (OSError, TransportError):
            continue
        try:
            cleanup_media(board)
        except (OSError, TransportError) as error:
            print(f"board: media cleanup failed: {error}", file=sys.stderr)
        return
    raise BoardRecoveryError("raw REPL recovery failed; unplug power and reconnect K230")


def run_script(board, source, timeout, emit):
    """Execute source and read the two raw REPL output streams."""
    deadline = time.monotonic() + timeout

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
    except (Exception, KeyboardInterrupt):
        recover(board)
        raise
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
    parser.add_argument("--port", default="auto", help="auto or explicit USB serial port")
    parser.add_argument("--dtr", choices=("default", "0", "1"), default="default")
    parser.add_argument("--rts", choices=("default", "0", "1"), default="default")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("ports", "info", "backup", "cleanup", "reset"):
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
    return parser.parse_args()


def main():
    args = arguments()
    if args.command == "ports":
        detect_ports()
        return 0

    try:
        device = select_port(args.port)
        try:
            if args.dtr == args.rts == "default":
                board = SerialTransport(device, timeout=1)
            else:
                board = ControlledSerialTransport(device, args.dtr, args.rts)
        except (OSError, TransportError) as error:
            raise TransportError(str(error) + "; close CanMV IDE and other serial tools") from error
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
                require_backup()
                upload(board, args.local, args.remote)
            elif args.command == "run":
                if not args.script.is_file():
                    raise FileNotFoundError(str(args.script))
                require_backup()
                run_command(board, args)
            elif args.command == "cleanup":
                print(cleanup_media(board))
            elif args.command == "reset":
                board.exit_raw_repl()
                board.serial.write(b"\x04")
        finally:
            try:
                if board.in_raw_repl:
                    board.exit_raw_repl()
            except OSError:
                pass
            finally:
                try:
                    board.close()
                except OSError:
                    board.serial.close()
    except BoardRecoveryError as error:
        print(f"board: {error}", file=sys.stderr)
        return 3
    except (OSError, ValueError, TransportError) as error:
        print(f"board: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
