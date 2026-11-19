import json

import pytest

from tools.analysis.exp01_camera import cases, summarize


def markers(name, **fields):
    return "HCV_EXP01 " + json.dumps({"event": name, **fields})


def test_camera_matrix_has_all_48_distinct_combinations():
    planned = list(cases())
    assert len(planned) == len(set(planned)) == 48


def test_summary_recalculates_frame_rate_and_percentiles():
    raw = "\n".join([
        markers("mode", actual=[1280, 720, 90], sensor="gc2093_csi2"),
        "HCV_FRAME,0,10000,9800",
        "HCV_FRAME,1,20000,9900",
        "HCV_FRAME,2,34000,13900",
        markers("image", width=640, height=360),
        markers("done", expected=3, acquired=3),
    ])
    result = summarize(raw)
    assert result["status"] == "ok"
    assert result["fps"] == pytest.approx(2_000_000 / 24_000)
    assert result["interval_p50"] == 10_000
    assert result["interval_p99"] == 14_000
    assert result["snapshot_p99"] == 13_900


def test_sensor_fallback_is_not_counted_as_requested_mode():
    raw = "\n".join([
        markers("mode", actual=[1920, 1080, 30], requested=[1920, 1080, 60]),
        markers("unsupported", reason="sensor selected a different physical mode"),
        markers("done", expected=1000, acquired=0),
    ])
    assert summarize(raw)["status"] == "unsupported"


def test_cleanup_error_stops_the_matrix_even_if_capture_succeeded():
    raw = markers("cleanup_error", stage="MediaManager.deinit", detail="busy")
    assert summarize(raw) == {"status": "cleanup-error", "detail": "busy", "frames": 0}
