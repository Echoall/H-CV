"""Computer-side checks for the manual boot prototype.

A hung init leaves the STARTING record written at power-on. The next boot
still starts when that streak is 1. The boot that reads streak 2 enters
maintenance. The watchdog stays off until 90 healthy frames have stored RUNNING.
"""

from pathlib import Path

from k230.hcv.proto import crc8
from k230.hcv.boot import (
    ERR_STREAM,
    ERR_UNKNOWN,
    HEALTHY_FRAMES,
    SKIP_FILE,
    STATE_BOOT,
    STATE_FAULT,
    WDT_TIMEOUT_S,
    BootSession,
    DirectoryStore,
    ManualClock,
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


def _session(store, ctrl_c=False, link=None):
    if link is None:
        link = StatusLink()
    watchdog = SimulatedWatchdog()
    session = BootSession(store, link, watchdog, ctrl_c=ctrl_c)
    return session, watchdog, link


def _healthy(session, count=HEALTHY_FRAMES, clock=None):
    if clock is None:
        clock = ManualClock()
    return session.run_frames(clock, lambda: None, count=count, deadline_s=30.0)


def _statuses(link):
    return [frame[5] for frame in link.sent]


def test_hung_init_counts_once_and_the_third_boot_maintains():
    store = MemoryStore()
    first, watchdog, _link = _session(store)
    started = first.run()
    assert started.mode == "starting"
    assert started.record.phase == "STARTING"
    assert started.record.streak == 1
    assert started.allow_camera is True
    assert watchdog.enabled is False
    # Init never returns, so RUNNING is not written.

    second, watchdog2, _link2 = _session(store)
    again = second.run()
    assert again.loaded.streak == 1
    assert again.mode == "starting"
    assert again.record.streak == 2
    assert watchdog2.enabled is False

    third, watchdog3, link3 = _session(store)
    held = third.run(heartbeats=3)
    assert held.loaded.streak == 2
    assert held.mode == "maintenance"
    assert held.wrote is False
    assert held.allow_camera is False
    assert watchdog3.enabled is False
    assert _statuses(link3) == [STATE_FAULT, STATE_FAULT, STATE_FAULT]
    assert link3.sent[0][6] == ERR_UNKNOWN
    assert store.commits == 2


def test_camera_init_runs_while_starting_and_before_the_watchdog():
    store = MemoryStore()
    watchdog = SimulatedWatchdog()
    order = []
    original_enable = watchdog.enable

    def enable(timeout_s, now=0.0):
        order.append("enable")
        original_enable(timeout_s, now)

    watchdog.enable = enable
    session = BootSession(store, StatusLink(), watchdog)
    assert session.run().mode == "starting"

    def camera():
        order.append(load_record(store).phase)
        assert watchdog.enabled is False

    session.import_camera(camera)
    assert order == ["STARTING"]
    assert _healthy(session) == HEALTHY_FRAMES
    assert order == ["STARTING", "enable"]
    assert session.mode == "measure"
    assert watchdog.timeout_s == WDT_TIMEOUT_S
    assert watchdog.feeds == 1
    assert load_record(store).phase == "RUNNING"
    assert load_record(store).streak == 0


def test_running_power_loss_starts_again_at_streak_one():
    store = MemoryStore()
    done, _watchdog, _link = _session(store)
    assert done.run().mode == "starting"
    assert _healthy(done) == HEALTHY_FRAMES
    assert load_record(store).phase == "RUNNING"

    lost, watchdog, _link2 = _session(store)
    result = lost.run()
    assert result.loaded.phase == "RUNNING"
    assert result.mode == "starting"
    assert result.record.streak == 1
    assert watchdog.enabled is False


def test_corrupt_commit_is_one_starting_and_a_torn_temp_is_ignored():
    store = MemoryStore()
    store.files["boot.state"] = b"HCVBOOT1 STARTING\n"
    measured, watchdog, _link = _session(store)
    result = measured.run()
    assert result.loaded.corrupt is True
    assert result.loaded.phase == "STARTING"
    assert result.loaded.streak == 1
    assert result.mode == "starting"
    assert result.record.streak == 2
    assert watchdog.enabled is False

    follow, watchdog2, _link2 = _session(store)
    assert follow.run().mode == "maintenance"
    assert watchdog2.enabled is False

    torn = MemoryStore()
    torn.files["boot.state"] = _line("RUNNING", 0, 4)
    torn.files["boot.state.tmp"] = b"HCVB"
    seen = load_record(torn)
    assert seen.phase == "RUNNING"
    assert seen.generation == 4
    assert seen.corrupt is False


def test_complete_temp_survives_a_lost_rename_and_measurement_continues():
    store = MemoryStore()
    store.fail_after_temp = True
    session, watchdog, link = _session(store)
    result = session.run()
    assert result.mode == "starting"
    assert result.write_warning is True
    assert watchdog.enabled is False
    assert "boot.state" not in store.files
    assert store.commits == 1
    assert link.sent[-1][5] == STATE_BOOT
    assert link.sent[-1][6] == ERR_UNKNOWN
    kept = load_record(store)
    assert kept.phase == "STARTING"
    assert kept.streak == 1
    assert kept.corrupt is False
    assert session.run_frames(ManualClock(), lambda: None, count=1, deadline_s=1.0) == 1
    assert watchdog.enabled is False


def test_skip_and_ctrl_c_escape_without_a_write_or_watchdog():
    store = MemoryStore()
    store.files[SKIP_FILE] = b""
    skipped, watchdog, link = _session(store)
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


def test_init_taking_three_seconds_does_not_arm_the_watchdog():
    store = MemoryStore()
    session, watchdog, _link = _session(store)
    assert session.run().mode == "starting"
    clock = ManualClock()

    def init():
        clock.sleep(3.0)

    session.import_camera(init)
    assert watchdog.enabled is False
    assert watchdog.observe(clock.now) is False
    assert watchdog.expired is False
    assert watchdog.resets == 0
    assert session.mode == "starting"
    assert load_record(store).phase == "STARTING"


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
    session, _watchdog, _link = _session(store)
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


def test_maintenance_holds_fault_at_two_hertz_until_the_guard():
    store = MemoryStore()
    store.files["boot.state"] = _line("STARTING", 2, 3)
    session, watchdog, link = _session(store)
    assert session.run(heartbeats=0).mode == "maintenance"
    session.hold(ManualClock(), deadline_s=1.0)
    assert _statuses(link) == [STATE_FAULT, STATE_FAULT]
    assert watchdog.enabled is False
    assert watchdog.resets == 0
    assert store.commits == 0


def test_three_frame_faults_leave_starting_and_do_not_arm_the_watchdog():
    store = MemoryStore()
    session, watchdog, link = _session(store)
    assert session.run(heartbeats=0).mode == "starting"

    def frame():
        raise RuntimeError("injected before the frame finished")

    completed = session.run_frames(ManualClock(), frame, count=8, deadline_s=5.0)
    assert completed == 0
    assert session.mode == "faulted"
    assert watchdog.feeds == 0
    assert watchdog.enabled is False
    assert watchdog.resets == 0
    assert store.commits == 1
    assert load_record(store).phase == "STARTING"
    session.hold(ManualClock(), deadline_s=0.5)
    assert _statuses(link) == [STATE_FAULT]


def test_ninety_healthy_frames_after_one_fault_then_feed():
    store = MemoryStore()
    session, watchdog, _link = _session(store)
    assert session.run().mode == "starting"
    seen = {"n": 0}

    def frame():
        seen["n"] += 1
        if seen["n"] == 1:
            raise RuntimeError("one injected fault")
        return None

    completed = session.run_frames(
        ManualClock(), frame, count=HEALTHY_FRAMES, deadline_s=30.0,
    )
    assert completed == HEALTHY_FRAMES
    assert session.mode == "measure"
    assert watchdog.feeds == 1
    assert store.commits == 2
    assert load_record(store).streak == 0


def test_a_slow_frame_after_arming_expires_without_a_hardware_reset():
    store = MemoryStore()
    session, watchdog, _link = _session(store)
    assert session.run().mode == "starting"
    clock = ManualClock()
    assert _healthy(session, clock=clock) == HEALTHY_FRAMES
    feeds = watchdog.feeds

    def frame():
        clock.sleep(WDT_TIMEOUT_S)
        return None

    completed = session.run_frames(clock, frame, count=1, deadline_s=30.0)
    assert completed == 0
    assert session.mode == "watchdog_expired"
    assert watchdog.expired is True
    assert watchdog.feeds == feeds
    assert watchdog.resets == 0


def test_stream_error_on_the_ninetieth_frame_still_arms_and_feeds():
    store = MemoryStore()
    session, watchdog, link = _session(store)
    assert session.run().mode == "starting"
    seen = {"n": 0}

    def frame():
        seen["n"] += 1
        if seen["n"] == HEALTHY_FRAMES:
            return "stream_error"
        return None

    completed = session.run_frames(ManualClock(), frame, count=HEALTHY_FRAMES, deadline_s=30.0)
    assert completed == HEALTHY_FRAMES
    assert session.mode == "measure"
    assert watchdog.feeds == 1
    assert store.commits == 2
    assert link.sent[-1][6] == ERR_STREAM
    assert link.sent[-1][12] == 3


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
