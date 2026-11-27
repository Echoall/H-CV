import pytest

from k230.hcv import media_session_guard


def test_claim_allows_one_session(monkeypatch, tmp_path):
    marker = tmp_path / "media-used"
    monkeypatch.setattr(media_session_guard, "_PRIMARY_MARKER", str(marker))
    monkeypatch.setattr(media_session_guard, "_now_ticks", lambda: 1000)

    media_session_guard.claim()

    assert marker.read_text() == "1000"
    with pytest.raises(RuntimeError, match="HCV_MEDIA_SESSION_ALREADY_USED"):
        media_session_guard.claim()


def test_claim_replaces_marker_from_previous_boot(monkeypatch, tmp_path):
    marker = tmp_path / "media-used"
    marker.write_text("5000")
    monkeypatch.setattr(media_session_guard, "_PRIMARY_MARKER", str(marker))
    monkeypatch.setattr(media_session_guard, "_now_ticks", lambda: 1000)

    media_session_guard.claim()

    assert marker.read_text() == "1000"


def test_claim_rejects_corrupt_marker(monkeypatch, tmp_path):
    marker = tmp_path / "media-used"
    marker.write_text("not-a-tick")
    monkeypatch.setattr(media_session_guard, "_PRIMARY_MARKER", str(marker))

    with pytest.raises(RuntimeError, match="HCV_MEDIA_SESSION_MARKER_INVALID"):
        media_session_guard.claim()


def test_claim_uses_fallback_when_tmp_is_missing(monkeypatch, tmp_path):
    marker = tmp_path / "run" / "media-used"
    marker.parent.mkdir()
    monkeypatch.setattr(media_session_guard, "_FALLBACK_MARKER", str(marker))
    monkeypatch.setattr(media_session_guard.os, "stat",
                        lambda path: (_ for _ in ()).throw(OSError()) if path == "/tmp"
                        else None)
    monkeypatch.setattr(media_session_guard, "_now_ticks", lambda: 1000)

    media_session_guard.claim()

    assert marker.read_text() == "1000"


def test_claim_fails_closed_when_fallback_directory_is_missing(monkeypatch, tmp_path):
    marker = tmp_path / "missing" / "media-used"
    monkeypatch.setattr(media_session_guard, "_FALLBACK_MARKER", str(marker))
    monkeypatch.setattr(media_session_guard.os, "stat",
                        lambda path: (_ for _ in ()).throw(OSError()) if path == "/tmp"
                        else None)

    with pytest.raises(RuntimeError, match="HCV_MEDIA_SESSION_GUARD_UNAVAILABLE"):
        media_session_guard.claim()


def test_claim_fails_closed_when_marker_cannot_be_written(monkeypatch, tmp_path):
    marker = tmp_path / "media-used"
    monkeypatch.setattr(media_session_guard, "_PRIMARY_MARKER", str(marker))
    real_open = open

    def failing_open(path, mode="r", *args, **kwargs):
        if path == str(marker) and "w" in mode:
            raise OSError("write failed")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    with pytest.raises(RuntimeError, match="HCV_MEDIA_SESSION_GUARD_UNAVAILABLE"):
        media_session_guard.claim()
