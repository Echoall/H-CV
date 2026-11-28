import numpy as np
import pytest

from tools.analysis.m3_player_timing import COLS, HEIGHT, ROWS, WIDTH, identify, marker, summarize


def test_markers_use_both_coordinates_and_wrap():
    image = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    image[16:24, 24:32] = 215
    assert identify(image) == COLS + 2
    marker(image, 0)
    assert identify(image) is None
    image.fill(95)
    assert identify(image) is None
    marker(image, COLS * ROWS + 1)
    assert identify(image) == 1


def test_display_age_excludes_warmup_and_counts_missing_source_ids():
    rows = [(1, 0, 10_000_000), (105, 2_000_000_000, 2_020_000_000),
            (105, 2_000_000_000, 2_021_000_000), (108, 2_060_000_000, 2_100_000_000)]
    result = summarize(rows, 0, 2)
    assert result["frames"] == 2
    assert result["missing"] == 2
    assert result["p50_age_ms"] == 30
    assert result["max_gap_ms"] == 80
    with pytest.raises(RuntimeError, match="out-of-order"):
        summarize(rows + [(99, 2_001_000_000, 2_110_000_000)], 0, 0)
