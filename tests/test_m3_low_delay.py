from pathlib import Path
from types import SimpleNamespace

import pytest

from station.bench import low_delay, player_compare


@pytest.mark.parametrize("drop", (True, False))
def test_display_gets_only_the_video_pipe(drop):
    receiver, player = low_delay.commands("ffmpeg", "ffplay", "rtsp://localhost:8554/cam", drop)
    assert receiver.count("-i") == 1 and receiver[receiver.index("-i")+1] == "rtsp://localhost:8554/cam"
    assert receiver.count("copy") == 2 and receiver.count("recording.ts") == 1
    assert receiver[-1] == "pipe:1" and player[-1] == "pipe:0"
    assert not any("rtsp" in arg for arg in player)
    assert ("-framedrop" in player) is drop
    assert ("-noframedrop" in player) is not drop


def test_exact_receive_gap_and_duration_thresholds():
    rows = [(i * 1_000_000_000, 0, i) for i in range(61)]
    assert low_delay.continuity(rows)["continuous_60s"]
    assert not low_delay.continuity(rows[:-1] + [(59_999_999_999, 0, 60)])["continuous_60s"]
    split = rows[:30] + [(s+1, t, n) for s, t, n in rows[30:]]
    assert not low_delay.continuity(split)["continuous_60s"]
    assert low_delay.continuity(split)["longest_s"] == 30
    with pytest.raises(ValueError):
        low_delay.continuity([rows[1], rows[0]])


@pytest.mark.parametrize("url", ("http://example:80/x", "rtsp://example/x", "rtsp://example:8554/"))
def test_bad_receiver_input_leaves_no_run_directory(tmp_path, url):
    stage = tmp_path / "invalid"
    with pytest.raises(ValueError):
        low_delay.receive(url, stage, "ffmpeg", "ffplay", 1, False)
    assert not stage.exists()


@pytest.mark.parametrize("name", ("vlc200", "ffplay"))
@pytest.mark.parametrize("diagnostic", (True, False))
def test_arrival_recording_and_replay_gate(tmp_path, monkeypatch, name, diagnostic):
    path = tmp_path / ("recording.mp4" if name == "vlc200" else "single/recording.mp4")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"fixture")
    cap = dict(frames=3600, exit_code=0, transport_observed=["tcp"], video_track_seconds=61, width=640, height=360,
               receiver_path="rtp_relay" if diagnostic else "direct")
    if diagnostic:
        cap.update(rtp_discontinuities=0, arrival={"continuous_60s": True})
    run = SimpleNamespace(directory=tmp_path, summary={"plan":{"receiver_ab":name, "rtp_diagnostic":diagnostic}, "capture":cap},
                          command=lambda *args, **kwargs: (0, "", ""))
    monkeypatch.setattr(player_compare, "frame_numbers", lambda *_: {"reference_pictures":3600, "gap_pct":0})
    assert player_compare.evaluate(run, True, Path("ffmpeg"))
    assert not player_compare.evaluate(run, False, Path("ffmpeg"))
    run.command = lambda *args, **kwargs: (1, "", "")
    assert not player_compare.evaluate(run, True, Path("ffmpeg"))
    run.command = lambda *args, **kwargs: (0, "", "")
    if diagnostic:
        cap["arrival"]["continuous_60s"] = False
    else:
        cap["receiver_path"] = "rtp_relay"
    assert not player_compare.evaluate(run, True, Path("ffmpeg"))


def test_direct_does_not_open_a_relay_or_write_arrival_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(low_delay, "trace", lambda *_a, **_k: pytest.fail("direct mode started a relay"))
    result = {}
    with low_delay.receiver_path("rtsp://localhost:8554/cam", tmp_path, False, result) as url:
        assert url == "rtsp://localhost:8554/cam"
    assert result == {"receiver_path": "direct"}
    assert not (tmp_path / "arrival.json").exists()


def test_failed_remux_preserves_ts_and_removes_partial_mp4(tmp_path, monkeypatch):
    source = tmp_path / "recording.ts"
    source.write_bytes(b"preserve")
    def fail(command, **options):
        assert command[command.index("-c")+1] == "copy"
        (tmp_path / command[-1]).write_bytes(b"partial")
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(low_delay.subprocess, "run", fail)
    assert not low_delay.remux_mp4(tmp_path, "ffmpeg")
    assert source.read_bytes() == b"preserve"
    assert not list(tmp_path.glob("*.mp4"))


def test_ending_comparison_before_first_round_never_accesses_board(tmp_path, monkeypatch):
    monkeypatch.setattr(player_compare.bench, "tools", lambda: ("vlc", None, None))
    monkeypatch.setattr(low_delay, "binaries", lambda: ("ffmpeg", "ffplay"))
    monkeypatch.setattr(player_compare.wlan, "snapshot", lambda: pytest.fail("attempted network access"))
    monkeypatch.setattr(player_compare.hubei, "physical", lambda *args: pytest.fail("attempted board access"))
    args = SimpleNamespace(simulate=None, runs=tmp_path, framedrop=False)
    assert player_compare.wizard(args, lambda _: "q") == 1
