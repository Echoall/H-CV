import json
from pathlib import Path

import pytest

from k230.hcv.config import calibration_fingerprint
from k230.hcv.geometry import interpolate_lut
from tools.analysis.m2_ball_calibration import synthetic_ball_calibration
from tools.calib import m2_field
from tools.calib.m2_field import static_result, transition_result
from tools.calib.ruler_lut import BALL_POSITIONS_MM, build_ball_calibration
from tools.synth.ruler import generate_ruler


def test_static_acceptance_needs_at_least_100_valid_positions_and_2mm_mean_error():
    invalid = {"status": 0, "pos_filt": -32768}
    valid = {"status": 5, "pos_filt": -482}
    assert not static_result([valid] * 99 + [invalid] * 2, -50)["passed"]
    result = static_result([valid] * 100 + [invalid] * 2, -50)
    assert result["mean_mm"] == -48.2
    assert result["std_mm"] == 0
    assert result["passed"]
    assert not static_result([valid] * 100, -51)["passed"]
    assert static_result([invalid] * 100, None)["passed"]
    assert not static_result([invalid] * 100 + [valid], None)["passed"]


def test_transitions_use_synchronised_frame_number_and_check_capture_gaps():
    rows = [{"status": 0}] * 32 + [{"status": 1}] * 100 + [{"status": 0}] * 20
    assert transition_result(rows, 30, True, 12, 90)["frames_including_change"] == 3
    assert transition_result(rows, 30, True, 12, 90)["passed"]
    assert not transition_result(rows, 29, True, 12, 90)["passed"]
    assert not transition_result(rows, 30, True, 23, 90)["passed"]
    assert transition_result(rows, 124, False, 12, 90)["passed"]
    assert not transition_result(rows, 123, False, 12, 90)["passed"]


def test_numbering_preview_is_saved_before_rejection(tmp_path, monkeypatch):
    image, _ = generate_ruler(0.4)
    calibration = {"markers_ref": [
        {"x_mm": -10, "u_px": 303, "v_px": 106},
        {"x_mm": 0, "u_px": 320, "v_px": 106},
        {"x_mm": 10, "u_px": 337, "v_px": 106},
    ]}
    labels = []
    monkeypatch.setattr(m2_field.cv2, "imshow", lambda _title, _view: None)
    monkeypatch.setattr(m2_field.cv2, "waitKey", lambda _delay: ord("n"))
    monkeypatch.setattr(m2_field.cv2, "destroyAllWindows", lambda: None)
    monkeypatch.setattr(m2_field.cv2, "putText",
                        lambda _img, text, *_args: labels.append(text))
    assert not m2_field.confirm_numbering(image, calibration, tmp_path)
    assert labels == ["-1", "0", "1"]
    assert (tmp_path / "numbered-ruler.png").stat().st_size > 0


@pytest.mark.parametrize("offset", (-10, 10))
def test_wrong_tick_numbering_never_replaces_active_calibration(tmp_path, monkeypatch, offset):
    config = json.loads((
        Path(__file__).resolve().parents[1] / "reports/data/M1/m1-config.json"
    ).read_text())
    image, truth = generate_ruler(0.4, scale_px_per_mm=1.4, seed=12)
    reference = {
        "samples": 60, "bg_profile": [150] * 640, "config": config,
        "fingerprint": calibration_fingerprint(config),
    }
    output = tmp_path / "test"
    uploads = []
    real_zero_px = truth[0]
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    monkeypatch.setattr(m2_field, "capture_reference", lambda _port, _output: (image, reference))
    monkeypatch.setattr(m2_field, "confirm_numbering", lambda *_args: True)
    monkeypatch.setattr(m2_field.subprocess, "run",
                        lambda command, check: uploads.append(command[-1]))

    def record_zero(_port, directory, *, calibration_path):
        assert directory.name == "normal_+0mm"
        assert calibration_path.endswith("/calib.pending.json")
        pending = json.loads((output / "calib.pending.json").read_text())
        pixels = [anchor["u_px"] for anchor in pending["anchors"]]
        positions = [anchor["x_mm"] for anchor in pending["anchors"]]
        measured_zero = interpolate_lut(real_zero_px, pixels, positions)
        assert abs(measured_zero) == pytest.approx(10, abs=0.3)
        return [{"status": 5, "pos_filt": round(measured_zero * 10)}] * 110, {}

    monkeypatch.setattr(m2_field, "record", record_zero)
    strip = (round(truth[-120]) + offset, round(truth[120]) + offset, 102, 111)
    with pytest.raises(ValueError, match="zero check failed"):
        m2_field.field_run("auto", output, strip, 0)
    assert uploads == ["/sdcard/hcv/calib.pending.json"]
    assert not (output / "calib.json").exists()
    results = json.loads((output / "results.json").read_text())
    assert not results["normal_+0mm"]["zero_check_passed"]


def ball_fixture():
    config = json.loads((
        Path(__file__).resolve().parents[1] / "reports/data/M1/m1-config.json"
    ).read_text())
    reference = {
        "samples": 60, "bg_profile": [150] * config["roi"]["w"], "config": config,
        "fingerprint": calibration_fingerprint(config),
    }
    points = {mm: [320 + 1.7 * mm] * 60 for mm in BALL_POSITIONS_MM}
    return config, reference, points


def test_ball_lut_from_seven_sets_of_camera_images():
    evidence = synthetic_ball_calibration()
    assert evidence["method"] == "ball"
    assert [a["x_mm"] for a in evidence["anchors"]] == list(BALL_POSITIONS_MM)
    assert {a["n"] for a in evidence["anchors"]} == {60}
    assert len(evidence["per_mm_errors"]) == 241
    assert all(abs(point["error_mm"]) < 0.5 for point in evidence["per_mm_errors"])


@pytest.mark.parametrize("case, error", [
    ("missing point", "seven distinct positions"),
    ("short sample", "at least 60 detected frames"),
    ("unstable", "unstable"),
    ("nonmonotonic", "must increase"),
    ("nonfinite", "invalid detector columns"),
])
def test_ball_calibration_refuses_bad_anchors(case, error):
    config, reference, points = ball_fixture()
    if case == "missing point":
        del points[-100]
    elif case == "short sample":
        points[50] = [points[50][0]] * 59 + [None] * 61
    elif case == "unstable":
        points[0] = [319.5, 320.5] * 30
    elif case == "nonmonotonic":
        points[50] = points[0]
    else:
        points[0][0] = float("nan")
    with pytest.raises(ValueError, match=error):
        build_ball_calibration(points, reference, config, 0)


def test_ball_mode_retries_only_the_unstable_point(tmp_path, monkeypatch):
    config, reference, points = ball_fixture()
    attempts = []

    def samples(_port, directory, fingerprint):
        assert fingerprint == reference["fingerprint"]
        attempts.append(directory.name)
        if directory.name == "-120mm-1":
            return [115, 116] * 30
        mm = int(directory.name.split("mm")[0])
        return points[mm]

    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    monkeypatch.setattr(m2_field, "capture_ball_samples", samples)
    assert m2_field.collect_ball_points("auto", tmp_path / "points", reference["fingerprint"]) == points
    assert attempts == ["-120mm-1", "-120mm-2"] + [f"{mm:+d}mm-1" for mm in BALL_POSITIONS_MM[1:]]


@pytest.mark.parametrize("zero_mm, succeeds", [(2.9, True), (3.0, False)])
def test_ball_field_mode_has_independent_zero_and_compares_fresh_samples(
        tmp_path, monkeypatch, zero_mm, succeeds):
    config, reference, points = ball_fixture()
    ruler_points = {mm: [value + 1.7 for value in values] for mm, values in points.items()}
    ruler = build_ball_calibration(ruler_points, reference, config, 0)
    ruler["method"] = "ruler"
    fresh_points = {mm: [value + 0.34 for value in values] for mm, values in points.items()}
    output = tmp_path / "field"
    uploads = []
    captures = []
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    monkeypatch.setattr(m2_field, "capture_reference", lambda _port, _output: (None, reference))
    monkeypatch.setattr(m2_field.subprocess, "run",
                        lambda command, check: uploads.append(command[-1]))

    def collect(_port, directory, fingerprint):
        assert fingerprint == reference["fingerprint"]
        captures.append(directory.name)
        return fresh_points if directory.name == "comparison-points" else points

    def check_zero(_port, _directory, *, calibration_path):
        assert calibration_path.endswith("/calib.pending.json")
        assert uploads == ["/sdcard/hcv/calib.pending.json"]
        assert not (output / "calib.json").exists()
        return [{"status": 5, "pos_filt": round(zero_mm * 10)}] * 100, {}

    monkeypatch.setattr(m2_field, "collect_ball_points", collect)
    monkeypatch.setattr(m2_field, "record", check_zero)
    if succeeds:
        assert m2_field.field_run("auto", output, None, 0, "ball", ruler)
        assert uploads == ["/sdcard/hcv/calib.pending.json", "/sdcard/hcv/calib.json"]
        assert captures == ["ball-anchors", "comparison-points"]
        comparison = json.loads((output / "lut-comparison.json").read_text())
        assert [row["x_mm"] for row in comparison] == list(BALL_POSITIONS_MM)
        for row in comparison:
            assert row["ruler_error_mm"] == pytest.approx(-0.8)
            assert row["ball_error_mm"] == pytest.approx(0.2)
            assert row["ruler_minus_ball_mm"] == pytest.approx(-1.0)
    else:
        with pytest.raises(ValueError, match="zero check failed"):
            m2_field.field_run("auto", output, None, 0, "ball", ruler)
        assert uploads == ["/sdcard/hcv/calib.pending.json"]
        assert captures == ["ball-anchors"]
        assert not (output / "calib.json").exists()
