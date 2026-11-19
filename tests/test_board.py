import json
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from tools.board.board import backup_sdcard, project_path, run_script


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
        assert not soft_reset
        self.calls.append("reenter")


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
    assert board.calls == [b"program", b"\x03\x03", "exit", "reenter"]


def test_script_error_is_captured():
    from mpremote.transport import TransportError

    board = FakeBoard(b"\x04", b"Traceback\r\n\x04")
    output = bytearray()
    with pytest.raises(TransportError, match="board-side exception"):
        run_script(board, b"program", 3, output.extend)
    assert output == b"Traceback\r\n"


@pytest.mark.parametrize("path", [
    "relative.py", "/sdcard/main.py", "/sdcard/hcv", "/sdcard/hcv/../main.py",
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
            SimpleNamespace(name="old", st_mode=0x4000, st_size=0),
        ],
        "/sdcard/old": [SimpleNamespace(name="demo.py", st_mode=0, st_size=12)],
    }
    board = SimpleNamespace(fs_listdir=dirs.__getitem__, fs_readfile=lambda path: b"hello")
    backup_sdcard(board, tmp_path)
    entries = json.loads((tmp_path / "sdcard.json").read_text(encoding="utf-8"))
    assert [entry["path"] for entry in entries] == [
        "/sdcard/main.py", "/sdcard/old", "/sdcard/old/demo.py",
    ]
    assert (tmp_path / "main.py").read_bytes() == b"hello"
    dirs.clear()
    backup_sdcard(board, tmp_path)
