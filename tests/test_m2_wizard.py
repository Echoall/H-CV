import json
import subprocess
from types import SimpleNamespace

from station.bench import m2


def test_full_rehearsal_preserves_prompts_records_and_frame_boundaries(tmp_path):
    output = tmp_path / "run"
    assert m2.wizard(output, simulate=True) == 0
    results = json.loads((output / "measurements/results.json").read_text(encoding="utf-8"))
    expected = {"normal_%+dmm" % i for i in (-120, -100, -50, 0, 50, 100, 120)}
    assert set(results) == expected | {"off_+0mm", "side_+0mm", "normal_empty", "off_empty",
                                       "side_empty", "insertion", "removal"}
    for name in expected:
        assert results[name]["valid"] == 180 and results[name]["mean_error_mm"] == 0
    assert results["insertion"]["frames_including_change"] == 3
    assert results["removal"]["frames_including_change"] == 9
    assert all(row["passed"] for row in results.values())
    operator = json.loads((output / "operator.json").read_text(encoding="utf-8"))
    assert operator["cues"] == "LLLLLH" * 15
    assert (output / "measurements/numbered-ruler.png").is_file()


def test_no_board_creates_incomplete_report_without_capture(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("info", kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", timeout)
    output = tmp_path / "physical"
    assert m2.wizard(output) == 2
    report = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert not report["complete"] and report["cases"] == {} and "采集还没开始" in report["error"]


def test_v18_prepares_verified_config_and_keeps_selected_port(tmp_path, monkeypatch):
    inventory = ("(release='v1.8-0-gc2d1f5c', version='v1.8-0-gc2d1f5c on 2026-07-03', "
                 "machine='k230_canmv_hiwonder')")
    calls = []

    def subprocess_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=inventory)

    def prepare(run, *, board_command):
        assert run.directory.is_dir()
        calls.append(board_command)

    def field_run(port, output, strip, angle, *, config_directory):
        calls.append((port, strip, angle, config_directory))
        return True

    monkeypatch.setattr(m2.subprocess, "run", subprocess_run)
    monkeypatch.setattr(m2.m1_v18, "prepare", prepare)
    monkeypatch.setattr(m2.field, "field_run", field_run)
    monkeypatch.setattr("builtins.input", lambda _: "0.1")
    assert m2.wizard(tmp_path / "v18", port="COM3") == 0
    assert calls[0][-3:] == ["--port", "COM3", "info"]
    assert calls[1][-2:] == ["--port", "COM3"]
    assert calls[2] == ("COM3", None, 0.1, "/sdcard/hcv/m1_v18")
