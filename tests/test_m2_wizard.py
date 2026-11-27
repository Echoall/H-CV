import json
import subprocess

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
