import json

import numpy as np
import pytest
import soundfile as sf

from meeting import recover


def write_wav(p, seconds=0.3):
    p.parent.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0, seconds, int(16000 * seconds), endpoint=False)
    sf.write(p, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000, subtype="PCM_16")
    return p


def orphan_session(meeting_home, pid=999999):
    from meeting import paths
    paths.ensure_dirs()
    wav = write_wav(paths.tmp_dir() / "2026-08-28_0900_mic.wav")
    paths.session_file().write_text(
        json.dumps({
            "pid": pid, "id": "2026-08-28_0900", "source": "mic",
            "started_at": "2026-08-28T09:00:00", "device": "mic default",
            "wav_paths": [str(wav)],
        }),
        encoding="utf-8",
    )
    return wav


def orphan_session_as_written(meeting_home, pid=999999):
    """Session file PERSIS seperti cli.cmd_record / button tulis saat start:
    `wav_paths: []` (file session tidak pernah di-update saat merekam), PID mati,
    WAV incremental nyata sudah ada di tmp/."""
    from meeting import paths
    paths.ensure_dirs()
    wav = write_wav(paths.tmp_dir() / "2026-08-28_0900_mic.wav")
    paths.session_file().write_text(
        json.dumps({
            "pid": pid, "id": "2026-08-28_0900", "source": "mic",
            "started_at": "2026-08-28T09:00:00", "device": "mic default",
            "wav_paths": [],
        }),
        encoding="utf-8",
    )
    return wav


def test_find_orphans_detects_yatim(meeting_home):
    orphan_session(meeting_home)
    orphans = recover.find_orphans()
    assert len(orphans) == 1
    assert orphans[0]["id"] == "2026-08-28_0900"


def test_no_orphan_when_session_active(meeting_home):
    import os
    orphan_session(meeting_home, pid=os.getpid())  # PID hidup = sesi aktif
    assert recover.find_orphans() == []


def test_offer_recovery_confirmed_finalizes_and_enqueues(meeting_home, monkeypatch):
    from meeting import paths, queuer
    orphan_session(meeting_home)
    monkeypatch.setattr(queuer, "spawn_worker", lambda: None)  # jangan spawn worker nyata (race dgn assert)

    job = recover.offer_recovery(ui_confirm=lambda msg: True)
    assert job is not None and job["id"] == "2026-08-28_0900"
    assert job["duration_sec"] == pytest.approx(0.3)  # durasi asli, bukan 0 (WAV sudah dihapus finalize)
    # FLAC final ada, WAV dibersihkan oleh finalizer
    assert (paths.recordings_dir() / "2026-08-28_0900.opus").exists()
    pend = list(paths.queue_dir("pending").glob("*.json"))
    assert len(pend) == 1
    assert not paths.session_file().exists()


def test_offer_recovery_declined_moves_to_discarded(meeting_home):
    from meeting import paths
    wav = orphan_session(meeting_home)

    job = recover.offer_recovery(ui_confirm=lambda msg: False)
    assert job is None
    assert (paths.tmp_dir() / "discarded" / wav.name).exists()
    assert not wav.exists()
    assert not paths.session_file().exists()


def test_offer_recovery_no_orphan_returns_none(meeting_home):
    assert recover.offer_recovery(ui_confirm=lambda msg: True) is None


# ---- regresi Critical-1: shape session persis yang ditulis producer ----

def test_recovery_from_empty_wav_paths_globs_tmp(meeting_home, monkeypatch):
    """Sesi yatim PERSIS seperti cmd_record menulisnya (wav_paths: [], PID mati,
    WAV nyata di tmp/) → recovery harus tetap finalisasi & enqueue (via glob)."""
    from meeting import paths, queuer
    wav = orphan_session_as_written(meeting_home)
    monkeypatch.setattr(queuer, "spawn_worker", lambda: None)

    job = recover.offer_recovery(ui_confirm=lambda msg: True)

    assert job is not None and job["id"] == "2026-08-28_0900"
    assert job["duration_sec"] == pytest.approx(0.3)
    assert (paths.recordings_dir() / "2026-08-28_0900.opus").exists()
    assert len(list(paths.queue_dir("pending").glob("*.json"))) == 1
    assert not paths.session_file().exists()  # tidak wedged — session dibersihkan
    assert not wav.exists()  # WAV dimakan finalize (FLAC tersimpan)


def test_recovery_no_wavs_anywhere_returns_none_no_raise(meeting_home):
    """Sesi yatim tanpa WAV di mana pun (wav_paths kosong, tmp/ kosong) →
    offer_recovery TIDAK boleh raise; session file dihapus; return None."""
    from meeting import paths
    paths.ensure_dirs()
    paths.session_file().write_text(
        json.dumps({
            "pid": 999999, "id": "2026-08-28_0900", "source": "mic",
            "started_at": "2026-08-28T09:00:00", "device": "mic default",
            "wav_paths": [],
        }),
        encoding="utf-8",
    )

    job = recover.offer_recovery(ui_confirm=lambda msg: True)

    assert job is None
    assert not paths.session_file().exists()
    assert list(paths.tmp_dir().glob("*.wav")) == []
    assert not (paths.recordings_dir() / "2026-08-28_0900.flac").exists()


def test_recovery_declined_with_empty_wav_paths_moves_globs(meeting_home):
    """Tolak recovery pada shape producer (wav_paths: []) → WAV hasil glob tetap
    dipindah ke discarded/, session file dihapus."""
    from meeting import paths
    wav = orphan_session_as_written(meeting_home)

    job = recover.offer_recovery(ui_confirm=lambda msg: False)

    assert job is None
    assert (paths.tmp_dir() / "discarded" / wav.name).exists()
    assert not wav.exists()
    assert not paths.session_file().exists()


def test_recovery_finalize_failure_discards_and_unlinks(meeting_home, monkeypatch):
    """Finalize melempar (WAV korup) → tidak raise/wedge: WAV selamat ke
    discarded/ (tidak dihapus), session file dihapus."""
    from meeting import paths
    wav = orphan_session_as_written(meeting_home)

    def boom(wavs, out_stem):
        raise RuntimeError("disk penuh saat FLAC")

    monkeypatch.setattr(recover.finalizer, "finalize", boom)
    job = recover.offer_recovery(ui_confirm=lambda msg: True)

    assert job is None
    assert (paths.tmp_dir() / "discarded" / wav.name).exists()
    assert not wav.exists()
    assert not paths.session_file().exists()
    assert list(paths.queue_dir("pending").glob("*.json")) == []
