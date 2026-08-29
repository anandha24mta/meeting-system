import os

import pytest

from meeting import session


def test_start_writes_session_file(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    session.start({"source": "mic", "id": "2026-08-29_1400", "wav_paths": ["tmp/a.wav"], "device": "Mic"})
    data = session.active()
    assert data["pid"] == os.getpid()
    assert data["source"] == "mic"
    assert data["id"] == "2026-08-29_1400"


def test_second_start_rejected_while_active(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    session.start({"source": "mic", "id": "a", "wav_paths": [], "device": "d"})
    with pytest.raises(session.SessionActiveError, match="sesi rekam aktif"):
        session.start({"source": "sys", "id": "b", "wav_paths": [], "device": "d"})


def test_stale_session_not_active(meeting_home):
    """PID mati = yatim → active() None, start() boleh."""
    from meeting import paths
    paths.ensure_dirs()
    paths.session_file().write_text('{"pid": 999999, "id": "x", "source": "mic", "wav_paths": [], "device": "d", "started_at": "2026-08-29T14:00:00"}', encoding="utf-8")
    assert session.active() is None
    session.start({"source": "mic", "id": "y", "wav_paths": [], "device": "d"})  # tidak raise


def test_stop_flag_roundtrip(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    assert session.stop_requested() is False
    session.request_stop()
    assert session.stop_requested() is True
    session.clear_stop_flag()
    assert session.stop_requested() is False


def test_finish_returns_meta_and_cleans_up(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    session.start({"source": "mic", "id": "z", "wav_paths": ["tmp/z.wav"], "device": "d"})
    session.request_stop()
    meta = session.finish()
    assert meta["id"] == "z"
    assert not paths.session_file().exists()
    assert not paths.stop_flag().exists()
