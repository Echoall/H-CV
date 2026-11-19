import json
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from tools.board.board import BoardRecoveryError, backup_sdcard, project_path, run_script


class FakeBoard:
    def __init__(self, *streams):
        self.streams = iter(streams)
        self.calls = []
        self.serial = SimpleNamespace(write=lambda data: self.calls.append(data))

    def exec_raw_no_follow(self, source):
        self.calls.append(source)

    def read_until(self, _minimum, _ending, *, timeout, timeout_overall, data_consumer):
        assert timeout is None
        assert timeout_overall > 0
        stream = next(self.streams)
        for value in stream:
            data_consumer(bytes((value,)))
        return stream[-1:]

    def exit_raw_repl(self):
        self.calls.append("exit")

    def enter_raw_repl(self, *, soft_reset, timeout_overall):
        self.calls.append(("reenter", soft_reset, timeout_overall))

    def exec(self, source):
        assert "MediaManager" in source
        self.calls.append("cleanup")
        return b"media cleanup ok"


def test_stream_omits_delimiters():
    board = FakeBoard(b"first\r\nsecond\r\n\x04", b"\x04")
    output = bytearray()
    run_script(board, b"program", 3, output.extend)
    assert output == b"first\r\nsecond\r\n"
    assert board.calls == [b"program"]


def test_no_eof_times_out_and_recovers_without_reset():
    board = FakeBoard(b"working")
    output = bytearray()
    with pytest.raises(TimeoutError):
        run_script(board, b"program", 3, output.extend)
    assert output == b"working"
    assert board.calls == [b"program", b"\x03\x03", "exit", ("reenter", False, 3), "cleanup"]


def test_script_error_is_captured():
    from mpremote.transport import TransportError

    board = FakeBoard(b"\x04", b"Traceback\r\n\x04")
    output = bytearray()
    with pytest.raises(TransportError, match="board-side exception"):
        run_script(board, b"program", 3, output.extend)
    assert output == b"Traceback\r\n"


@pytest.mark.parametrize("path", [
    "relative.py", "/sdcard/main.py", "/sdcard/hcv", "/sdcard/hcv/../main.py",
    r"/sdcard/hcv/backslash\name.py",
])
def test_upload_path_restricted_to_project(path):
    with pytest.raises(ValueError):
        project_path(path)


def test_project_subdirectory_is_allowed():
    assert project_path("/sdcard/hcv/example/main.py") == PurePosixPath("/sdcard/hcv/example/main.py")


def test_backup_lists_nested_files_and_saves_original_startup(tmp_path):
    dirs = {
        "/sdcard": [
            SimpleNamespace(name="main.py", st_mode=0, st_size=5),
            SimpleNamespace(name="boot.py", st_mode=0, st_size=5),
            SimpleNamespace(name="old", st_mode=0x4000, st_size=0),
        ],
        "/sdcard/old": [SimpleNamespace(name="demo.py", st_mode=0, st_size=12)],
    }
    from hashlib import sha256

    board = SimpleNamespace(fs_listdir=dirs.__getitem__, fs_readfile=lambda path: b"hello",
                            fs_hashfile=lambda path, algorithm: sha256(b"other").digest())
    backup_sdcard(board, tmp_path)
    manifest = json.loads((tmp_path / "sdcard.json").read_text(encoding="utf-8"))
    assert [entry["path"] for entry in manifest["entries"]] == [
        "/sdcard/boot.py", "/sdcard/main.py", "/sdcard/old", "/sdcard/old/demo.py",
    ]
    assert manifest["file_count"] == 3
    assert manifest["total_bytes"] == 22
    assert manifest["entries"][0]["sha256"] == sha256(b"hello").hexdigest()
    assert manifest["entries"][-1]["sha256"] == sha256(b"other").hexdigest()
    assert (tmp_path / "main.py").read_bytes() == b"hello"
    assert (tmp_path / "boot.py").read_bytes() == b"hello"
    dirs.clear()
    backup_sdcard(board, tmp_path)


def test_auto_selects_only_known_canmv_port(monkeypatch):
    from tools.board import board as board_module

    def device(name, vid, pid):
        return SimpleNamespace(device=name, vid=vid, pid=pid)

    monkeypatch.setattr(board_module.list_ports, "comports",
                        lambda: [device("COM1", None, None), device("COM3", 0x1209, 0xABD1)])
    assert board_module.select_port("auto") == "COM3"
    assert board_module.select_port("COM8") == "COM8"

    monkeypatch.setattr(board_module.list_ports, "comports",
                        lambda: [device("COM3", 0x1209, 0xABD1),
                                 device("COM4", 0x1209, 0xABD1)])
    with pytest.raises(ValueError, match="found 2"):
        board_module.select_port("auto")


def test_timeout_when_output_never_stops(monkeypatch):
    from tools.board import board as board_module

    ticks = iter((0.0, 0.0, 0.5, 1.1))
    monkeypatch.setattr(board_module.time, "monotonic", lambda: next(ticks))
    board = FakeBoard(b"abcdefgh\n\x04")
    output = bytearray()
    with pytest.raises(TimeoutError):
        run_script(board, b"program", 1, output.extend)
    assert output == b"a"
    assert board.calls[-4:] == [b"\x03\x03", "exit", ("reenter", False, 3), "cleanup"]


def test_soft_reset_is_used_when_first_interrupt_fails():
    from mpremote.transport import TransportError

    board = FakeBoard(b"no EOF")
    original = board.enter_raw_repl

    def enter(*, soft_reset, timeout_overall):
        original(soft_reset=soft_reset, timeout_overall=timeout_overall)
        if not soft_reset:
            raise TransportError("board still busy")

    board.enter_raw_repl = enter
    with pytest.raises(TimeoutError):
        run_script(board, b"program", 1, lambda data: None)
    assert ("reenter", False, 3) in board.calls
    assert board.calls[-2:] == [("reenter", True, 10), "cleanup"]


def test_failed_recovery_requests_manual_power_cycle():
    from mpremote.transport import TransportError

    board = FakeBoard(b"no EOF")

    def fail_to_enter(**kwargs):
        raise TransportError("board still busy")

    board.enter_raw_repl = fail_to_enter
    with pytest.raises(BoardRecoveryError, match="unplug power and reconnect"):
        run_script(board, b"program", 1, lambda data: None)


@pytest.mark.parametrize("failure", [None, "hash", "rename"])
def test_upload_checks_before_replacing_and_preserves_previous_file(tmp_path, failure):
    from ast import literal_eval
    from hashlib import sha256
    from mpremote.transport import TransportError
    from tools.board.board import upload

    remote = "/sdcard/hcv/main.py"
    staged = "/sdcard/hcv/.main.py.hcv-upload"
    files = {remote: b"old"}
    local = tmp_path / "main.py"
    local.write_bytes(b"\x00\xff\x04\nnew")

    def write(path, data, chunk_size):
        assert chunk_size <= 256
        files[path] = data

    def digest(path, algorithm):
        assert algorithm == "sha256"
        return sha256(b"corrupt" if failure == "hash" else files[path]).digest()

    def execute(source):
        old, new = literal_eval(source.split("os.rename", 1)[-1])
        if failure == "rename" and old == staged:
            raise TransportError("staging rename failed")
        files[new] = files.pop(old)

    board = SimpleNamespace(
        fs_isdir=lambda path: path == "/sdcard/hcv",
        fs_exists=files.__contains__,
        fs_rmfile=files.pop,
        fs_hashfile=digest,
        fs_writefile=write,
        exec=execute,
    )
    if failure is None:
        upload(board, local, remote)
        assert files == {remote: local.read_bytes()}
    else:
        with pytest.raises((ValueError, TransportError)):
            upload(board, local, remote)
        assert files[remote] == b"old"
