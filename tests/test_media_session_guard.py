import pytest

from k230.hcv import media_session_guard


def test_claim_allows_one_session(monkeypatch, tmp_path):
    marker = tmp_path / "media-used"
    monkeypatch.setattr(media_session_guard, "_MARKER", str(marker))

    media_session_guard.claim()

    assert marker.read_text() == "1"
    with pytest.raises(RuntimeError, match="HCV_MEDIA_SESSION_ALREADY_USED"):
        media_session_guard.claim()


def test_claim_fails_closed_when_marker_cannot_be_written(monkeypatch, tmp_path):
    marker = tmp_path / "missing" / "media-used"
    monkeypatch.setattr(media_session_guard, "_MARKER", str(marker))

    with pytest.raises(RuntimeError, match="HCV_MEDIA_SESSION_GUARD_UNAVAILABLE"):
        media_session_guard.claim()
