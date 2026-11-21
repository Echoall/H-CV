"""Computer-side checks for the manual boot prototype.

Expected streaks come from the approved rule: one unfinished STARTING is a
single count, and the second consecutive one enters maintenance.
"""

from pathlib import Path

from k230.hcv.proto import crc8
from k230.hcv.boot import (
    ERR_STREAM,
    ERR_UNKNOWN,
    SKIP_FILE,
    STATE_FAULT,
    WDT_TIMEOUT_S,
    BootSession,
    DirectoryStore,
    MemoryStore,
    SimulatedWatchdog,
    StatusLink,
    load_record,
    parse_record,
)
from k230.boot_manual import run_manual


def _line(phase, streak, generation):
    body = "HCVBOOT1 %s %d %d" % (phase, streak, generation)
    return (body + " %02X\n" % crc8(body.encode("ascii"))).encode("ascii")


def _session(store, fault=None, ctrl_c=False, link=None, camera_import=None,
             heartbeats=1):
    if link is None:
        link = StatusLink()
    watchdog = SimulatedWatchdog()
    session = BootSession(
        store, link, watchdog, ctrl_c=ctrl_c, fault=fault,
        camera_import=camera_import,
    )
    return session, watchdog, link


def _statuses(link):
    return [frame[5] for frame in link.sent]


def test_one_fault_is_not_maintenance_and_the_second_is():
    store = MemoryStore()
    first, watchdog, _link = _session(store, fault="before_loop")
    failed = first.run()
    assert failed.mode == "failed"
    assert failed.wrote is True
    assert failed.record.phase == "STARTING"
    assert failed.record.streak == 1
    assert failed.allow_camera is False
    assert watchdog.enabled is False
    assert watchdog.feeds == 0

    second, watchdog2, link2 = _session(store, fault="before_loop", heartbeats=3)
    stopped = second.run(heartbeats=3)
    assert stopped.mode == "maintenance"
    assert stopped.record.streak == 2
    assert watchdog2.enabled is False
    assert _statuses(link2) == [STATE_FAULT, STATE_FAULT, STATE_FAULT]
    assert link2.sent[0][6] == ERR_UNKNOWN

    third, watchdog3, _link3 = _session(store, fault=None)
    held = third.run()
    assert held.mode == "maintenance"
    assert held.wrote is False
    assert held.allow_camera is False
    assert watchdog3.enabled is False
    assert store.commits == 2


def test_success_after_one_fault_clears_the_streak_and_arms_sim_wdt():
    store = MemoryStore()
    failed, _watchdog, _link = _session(store, fault="before_loop")
    assert failed.run().mode == "failed"

    order = []
    watchdog = SimulatedWatchdog()
    original_enable = watchdog.enable

    def enable(timeout_s):
        order.append(("enable", timeout_s))
        original_enable(timeout_s)

    watchdog.enable = enable

    def camera():
        order.append(("camera", load_record(store).phase))

    link = StatusLink()
    session = BootSession(store, link, watchdog, camera_import=camera)
    result = session.run()
    assert result.mode == "measure"
    assert result.allow_camera is True
    assert watchdog.timeout_s == WDT_TIMEOUT_S
    assert order == [("enable", 1.0), ("camera", "RUNNING")]
    session.on_frame_complete()
    assert watchdog.feeds == 1
    assert link.sent == []

    again, _watchdog2, _link2 = _session(store, fault="before_loop")
    assert again.run().mode == "failed"
    assert again.record.streak == 1


def test_running_power_loss_counts_once():
    store = MemoryStore()
    ok, _watchdog, _link = _session(store)
    assert ok.run().mode == "measure"
    lost, _watchdog2, _link2 = _session(store, fault="before_loop")
    result = lost.run()
    assert result.mode == "failed"
    assert result.record.streak == 1


def test_corrupt_commit_is_one_starting_and_a_torn_temp_is_ignored():
    store = MemoryStore()
    store.files["boot.state"] = b"HCVBOOT1 STARTING\n"
    measured, _watchdog, _link = _session(store)
    assert measured.run().mode == "measure"
    assert measured.record.phase == "RUNNING"
    assert measured.record.streak == 0

    torn = MemoryStore()
    torn.files["boot.state"] = _line("RUNNING", 0, 4)
    torn.files["boot.state.tmp"] = b"HCVB"
    seen = load_record(torn)
    assert seen.phase == "RUNNING"
    assert seen.generation == 4
    assert seen.corrupt is False

    both_bad = MemoryStore()
    both_bad.files["boot.state"] = b"??"
    both_bad.files["boot.state.tmp"] = b"??"
    assert load_record(both_bad).corrupt is True
    faulted, _watchdog2, _link2 = _session(both_bad, fault="before_loop")
    assert faulted.run().mode == "maintenance"
    assert faulted.record.streak == 2


def test_complete_temp_survives_a_lost_rename():
    store = MemoryStore()
    store.fail_after_temp = True
    session, watchdog, _link = _session(store, fault="before_loop")
    try:
        session.run()
    except OSError as error:
        assert "rename" in str(error)
    else:
        raise AssertionError("rename failure did not surface")
    assert watchdog.enabled is False
    assert "boot.state" not in store.files
    assert store.commits == 1
    kept = load_record(store)
    assert kept.phase == "STARTING"
    assert kept.streak == 1
    assert kept.corrupt is False


def test_skip_and_ctrl_c_escape_without_a_write_or_watchdog():
    store = MemoryStore()
    store.files[SKIP_FILE] = b""
    skipped, watchdog, link = _session(store, fault="before_loop")
    result = skipped.run()
    assert result.mode == "escape"
    assert result.wrote is False
    assert result.allow_camera is False
    assert watchdog.enabled is False
    assert store.commits == 0
    assert _statuses(link) == [STATE_FAULT]

    bare = MemoryStore()
    interrupted, watchdog2, link2 = _session(bare, ctrl_c=True)
    assert interrupted.run().mode == "escape"
    assert interrupted.wrote is False
    assert watchdog2.enabled is False
    assert link2.sent[0][5] == STATE_FAULT


def test_stream_error_does_not_write_or_stop_the_watchdog():
    store = MemoryStore()
    session, watchdog, link = _session(store)
    assert session.run().mode == "measure"
    commits = store.commits
    session.note_stream_error()
    session.on_frame_complete()
    assert store.commits == commits
    assert load_record(store).phase == "RUNNING"
    assert watchdog.enabled is True
    assert watchdog.feeds == 1
    assert link.sent[-1][6] == ERR_STREAM
    assert link.sent[-1][12] == 3


def test_unavailable_uart_still_refuses_maintenance_without_wdt():
    store = MemoryStore()
    store.files["boot.state"] = _line("STARTING", 2, 3)
    link = StatusLink(available=False)
    session, watchdog, _link = _session(store, link=link)
    result = session.run()
    assert result.mode == "maintenance"
    assert result.wrote is False
    assert link.sent == []
    assert watchdog.enabled is False


def test_parser_accepts_an_independently_checksummed_line():
    raw = _line("STARTING", 1, 7)
    record = parse_record(raw)
    assert record.phase == "STARTING"
    assert record.streak == 1
    assert record.generation == 7
    assert record.corrupt is False
    assert parse_record(raw[:-3] + b"00\n").corrupt is True


def test_directory_store_renames_one_record(tmp_path):
    store = DirectoryStore(str(tmp_path))
    session, _watchdog, _link = _session(store, fault="before_loop")
    result = session.run()
    assert result.record.streak == 1
    final = Path(tmp_path) / "boot.state"
    temporary = Path(tmp_path) / "boot.state.tmp"
    assert final.is_file()
    assert not temporary.exists()
    assert b"STARTING 1 " in final.read_bytes()
    assert store.commits == 1


def test_manual_entry_sees_ctrl_c_before_camera(tmp_path):
    calls = {"camera": 0, "sleep": 0}

    def camera():
        calls["camera"] += 1

    def sleep(_seconds):
        calls["sleep"] += 1

    result = run_manual(
        str(tmp_path),
        StatusLink(),
        interrupted=lambda: True,
        sleep=sleep,
        camera_import=camera,
        ctrl_c_window_s=1.5,
    )
    assert result.mode == "escape"
    assert result.wrote is False
    assert calls == {"camera": 0, "sleep": 0}
    assert not (Path(tmp_path) / "boot.state").exists()


def test_sources_do_not_import_camera_or_install_main_py():
    root = Path(__file__).resolve().parents[1]
    boot_text = (root / "k230/hcv/boot.py").read_text(encoding="utf-8")
    manual_text = (root / "k230/boot_manual.py").read_text(encoding="utf-8")
    for text in (boot_text, manual_text):
        assert "import sensor" not in text
        assert "from media" not in text
        assert "media.sensor" not in text
    assert "main.py" not in boot_text
    assert "Do not copy it to ``/sdcard/main.py``" in manual_text
