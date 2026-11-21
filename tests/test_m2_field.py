from tools.calib.m2_field import static_result, transition_result


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
