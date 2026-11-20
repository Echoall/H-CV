import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "k230"))

from tools.eval.evaluate import evaluate
from tools.synth.generate import generate_scene, write_scene


def test_synth_truth_and_ideal_evaluation(tmp_path):
    image, truth = generate_scene(
        ball_x=210,
        polarity="bright",
        highlight=True,
        scratch=True,
        magnet_shadow=True,
        motion_blur=2,
        seed=7,
    )
    assert image.shape == (360, 640)
    assert 0 <= truth["u_px"] < image.shape[1]
    dataset = tmp_path / "dataset"
    write_scene(dataset / "scene_a", count=5, ball_x=210, polarity="bright", seed=7)
    summary, _ = evaluate(dataset, detector_name="ideal")
    assert summary["frames"] == 5
    assert summary["valid_rate"] == 1.0
    assert summary["rms_error_px"] == 0.0
    assert summary["max_abs_error_px"] == 0.0


def test_profile_detector_runs_on_generated_scene(tmp_path):
    dataset = tmp_path / "dataset"
    write_scene(
        dataset / "scene_a",
        count=5,
        ball_x=280,
        polarity="dark",
        gradient=0.04,
        noise=1.0,
        highlight=True,
        magnet_shadow=True,
        seed=10,
    )
    summary, rows = evaluate(dataset, detector_name="profile")
    assert summary["frames"] == 5
    assert summary["valid_rate"] == 1.0
    assert summary["max_abs_error_px"] < 10.0
    assert all(row["quality"] >= 0 for row in rows)
