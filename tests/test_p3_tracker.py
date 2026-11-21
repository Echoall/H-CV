import math

from k230.hcv.tracker import KalmanTracker


PARAMS = {
    "q_accel": 2000.0,
    "r_meas": 0.3,
    "gate_mm": 15.0,
    "coast_max": 3,
    "reacquire_frames": 3,
}


def test_tracker_follows_constant_velocity():
    tracker = KalmanTracker(PARAMS)
    outputs = [
        tracker.step(10.0 * index, 1_000_000 + index * 11111)
        for index in range(30)
    ]
    assert all(output["valid"] for output in outputs)
    assert abs(outputs[-1]["position_mm"] - 290.0) < 2.0
    assert abs(outputs[-1]["velocity_mm_s"] - 900.0) < 100.0


def test_tracker_predicts_through_three_missing_frames_then_loses():
    tracker = KalmanTracker(PARAMS)
    tracker.step(0.0, 0)
    for index in range(1, 4):
        result = tracker.step(None, index * 11111)
        assert result["predicted"]
        assert result["valid"]
        assert not result["lost"]
    result = tracker.step(None, 4 * 11111)
    assert result["predicted"]
    assert not result["valid"]
    assert result["lost"]


def test_tracker_rejects_single_outlier_and_reacquires_real_jump():
    tracker = KalmanTracker(PARAMS)
    for index in range(5):
        tracker.step(float(index), index * 11111)
    outlier = tracker.step(100.0, 5 * 11111)
    assert outlier["predicted"]
    assert not outlier["reacquire"]
    tracker.step(100.0, 6 * 11111)
    reacquired = tracker.step(100.0, 7 * 11111)
    assert reacquired["reacquire"]
    assert reacquired["valid"]
    assert abs(reacquired["position_mm"] - 100.0) < 1.0


def test_tracker_handles_reversal_without_marking_it_lost():
    tracker = KalmanTracker(PARAMS)
    results = []
    for index in range(10):
        results.append(tracker.step(float(index * 3), index * 11111))
    for index in range(10, 25):
        results.append(tracker.step(float(27 - (index - 9) * 3), index * 11111))
    assert all(result["valid"] for result in results)
    assert not any(result["lost"] for result in results)


def test_tracker_handles_timestamp_wraparound():
    tracker = KalmanTracker(PARAMS)
    start = 0xFFFFFF00
    first = tracker.step(0.0, start)
    second = tracker.step(1.0, (start + 11111) & 0xFFFFFFFF)
    assert first["valid"]
    assert second["valid"]
    assert not math.isnan(second["position_mm"])


def test_tracker_reacquire_jump_does_not_infer_extreme_velocity():
    tracker = KalmanTracker(PARAMS)
    tracker.step(0.0, 0)
    tracker.step(None, 11111)
    tracker.step(None, 22222)
    tracker.step(80.0, 33333)
    tracker.step(80.0, 44444)
    result = tracker.step(80.0, 55555)
    assert result["reacquire"]
    assert abs(result["velocity_mm_s"]) < 50.0
    for index in range(4, 14):
        result = tracker.step(80.0, index * 11111)
    assert abs(result["position_mm"] - 80.0) < 1.0
    assert abs(result["velocity_mm_s"]) < 50.0


def test_tracker_two_rejected_measurements_remain_predicted_and_valid():
    tracker = KalmanTracker(PARAMS)
    tracker.step(0.0, 0)
    tracker.step(1.0, 11111)
    first = tracker.step(100.0, 22222)
    second = tracker.step(100.0, 33333)
    assert first["valid"] and first["predicted"]
    assert second["valid"] and second["predicted"]


def test_tracker_constant_acceleration_has_bounded_position_error():
    tracker = KalmanTracker(PARAMS)
    errors = []
    for index in range(60):
        time_s = index / 90.0
        truth = 0.5 * 600.0 * time_s * time_s
        result = tracker.step(truth, index * 11111)
        errors.append(abs(result["position_mm"] - truth))
    assert max(errors[5:]) < 2.5
