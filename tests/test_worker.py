import json
import time

import pytest

from meeting import queuer, worker


def make_job(**over):
    job = {
        "id": "2026-08-29_1400-standup",
        "created_at": "2026-08-29T14:00:05",
        "source": "mic",
        "audio": "recordings/2026-08-29_1400-standup.flac",
        "wav": None,
        "needs_finalization": False,
        "duration_sec": 60.0,
        "device": "Headset (Jabra)",
        "model": "gemini-flash",
        "status": "pending",
    }
    job.update(over)
    return job


@pytest.fixture
def flac(meeting_home):
    """FLAC kecil final di recordings/ — job tanpa finalisasi."""
    import numpy as np
    import soundfile as sf
    from meeting import paths
    paths.ensure_dirs()
    p = paths.recordings_dir() / "2026-08-29_1400-standup.flac"
    t = np.linspace(0, 0.5, 8000, endpoint=False)
    sf.write(p, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000, format="FLAC")
    return p


class FakeTranscriber:
    """Transcriber yang bisa diprogram gagal N kali pertama."""

    def __init__(self, fail_times=0, error=RuntimeError("network down")):
        self.fail_times = fail_times
        self.calls = 0
        self.error = error

    def __call__(self, audio_path, job, client=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        return {"status": "done", "id": job["id"]}


def test_run_once_empty_queue_returns_false(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    assert worker.run_once() is False


def test_run_once_success_moves_job_to_done(meeting_home, flac, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())
    monkeypatch.setattr(worker, "transcribe_audio", FakeTranscriber())

    assert worker.run_once() is True
    done = list(paths.queue_dir("done").glob("*.json"))
    assert len(done) == 1
    job = json.loads(done[0].read_text(encoding="utf-8"))
    assert job["status"] == "done"
    assert not list(paths.queue_dir("pending").glob("*.json"))
    assert not list(paths.queue_dir("pending").glob("*.processing"))


def test_run_once_failure_moves_job_to_failed(meeting_home, flac, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())
    monkeypatch.setattr(
        worker, "transcribe_audio", FakeTranscriber(fail_times=99)
    )

    assert worker.run_once() is True
    failed = list(paths.queue_dir("failed").glob("*.json"))
    assert len(failed) == 1
    job = json.loads(failed[0].read_text(encoding="utf-8"))
    assert job["status"] == "failed"
    assert "network down" in job["error"]
    assert not flac.with_suffix(".deleted").exists()
    assert flac.exists()  # audio TIDAK dihapus saat gagal


def test_rate_limit_retries_with_backoff(meeting_home, flac, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())

    sleeps = []
    monkeypatch.setattr(worker.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(
        worker, "transcribe_audio", FakeTranscriber(fail_times=2)
    )

    class RateLimitError(Exception):
        pass

    # error pertama & kedua = 429-ish (punya attr .code == 429)
    fl = FakeTranscriber(fail_times=2)
    fl.error = worker.RateLimitedError("429 quota")
    monkeypatch.setattr(worker, "transcribe_audio", fl)

    assert worker.run_once() is True
    assert fl.calls == 3  # 2 gagal + 1 sukses
    assert sleeps == [60, 300]
    assert list(paths.queue_dir("done").glob("*.json"))


def test_rate_limit_gives_up_after_two_retries(meeting_home, flac, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())

    sleeps = []
    monkeypatch.setattr(worker.time, "sleep", lambda s: sleeps.append(s))
    fl = FakeTranscriber(fail_times=99)
    fl.error = worker.RateLimitedError("429 quota")
    monkeypatch.setattr(worker, "transcribe_audio", fl)

    worker.run_once()
    assert sleeps == [60, 300]
    job = json.loads(
        list(paths.queue_dir("failed").glob("*.json"))[0].read_text(encoding="utf-8")
    )
    assert "429" in job["error"]


def test_finalize_failure_routes_job_to_failed(meeting_home, monkeypatch):
    """WAV hilang saat finalisasi → job ke failed/ (bukan loop tanpa batas di pending)."""
    from meeting import paths
    paths.ensure_dirs()
    job = make_job(wav="tmp/tidak_ada.wav", needs_finalization=True)
    queuer.enqueue(job)

    calls = []
    monkeypatch.setattr(
        worker, "transcribe_audio",
        lambda audio_path, job, client=None: calls.append(1) or {"status": "done"},
    )
    assert worker.run_once() is True
    assert calls == []  # transkripsi tidak pernah dipanggil
    failed = json.loads(
        list(paths.queue_dir("failed").glob("*.json"))[0].read_text(encoding="utf-8")
    )
    assert failed["status"] == "failed"
    assert "tidak_ada" in failed["error"]
    assert not list(paths.queue_dir("pending").glob("*.json"))
    from meeting import indexmd
    assert indexmd.rows()[0]["status"] == "failed"


def test_run_once_finalizes_wav_before_transcribe(meeting_home, monkeypatch, tmp_path):
    from meeting import finalizer as fin
    from meeting import paths
    paths.ensure_dirs()

    import numpy as np
    import soundfile as sf
    wav = paths.tmp_dir() / "2026-08-29_1400-standup_mic.wav"
    t = np.linspace(0, 0.3, 4800, endpoint=False)
    sf.write(wav, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000, subtype="PCM_16")

    job = make_job(
        wav="tmp/2026-08-29_1400-standup_mic.wav", needs_finalization=True
    )
    queuer.enqueue(job)

    calls = []
    monkeypatch.setattr(
        worker,
        "transcribe_audio",
        lambda audio_path, job, client=None: calls.append(str(audio_path)) or {"status": "done"},
    )
    worker.run_once()

    assert calls and "recordings" in calls[0] and calls[0].endswith(".m4a")
    assert not wav.exists()  # WAV dibersihkan setelah FLAC verify
    done_job = json.loads(
        list(paths.queue_dir("done").glob("*.json"))[0].read_text(encoding="utf-8")
    )
    assert done_job["needs_finalization"] is False


def test_index_updated_on_done(meeting_home, flac, monkeypatch):
    from meeting import indexmd, paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())
    monkeypatch.setattr(worker, "transcribe_audio", FakeTranscriber())
    worker.run_once()
    rows = indexmd.rows()
    assert rows and rows[0]["status"] == "done"


def test_index_updated_on_failed(meeting_home, flac, monkeypatch):
    from meeting import indexmd, paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())
    monkeypatch.setattr(worker, "transcribe_audio", FakeTranscriber(fail_times=99))
    worker.run_once()
    rows = indexmd.rows()
    assert rows and rows[0]["status"] == "failed"


def test_lock_single_worker(meeting_home, flac, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    lock = paths.home() / "data" / "worker.lock"
    lock.write_text("999999")  # PID tidak hidup → stale
    # stale lock dengan PID mati diambil alih
    assert worker._acquire_lock() is True
    assert worker._acquire_lock() is False  # kini hidup (PID kita)


def test_fatal_api_key_error_writes_last_error(meeting_home, flac, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())

    fl = FakeTranscriber(fail_times=99)
    fl.error = worker.FatalWorkerError("API key invalid")
    monkeypatch.setattr(worker, "transcribe_audio", fl)

    with pytest.raises(worker.FatalWorkerError):
        worker.run_once()
    err = (paths.home() / "data" / "worker.last_error").read_text(encoding="utf-8")
    assert "API key invalid" in err


def test_reclaim_processing_renames_leftovers(meeting_home):
    """Worker mati di tengah job meninggalkan .processing → sweep mengembalikan
    ke .json agar diproses lagi (bukan pending selamanya)."""
    from meeting import paths
    paths.ensure_dirs()
    left = paths.queue_dir("pending") / "j1.json.processing"
    left.write_text(json.dumps(make_job(id="j1")), encoding="utf-8")

    n = worker.reclaim_processing()

    assert n == 1
    assert not left.exists()
    assert (paths.queue_dir("pending") / "j1.json").exists()


def test_reclaim_processing_noop_when_clean(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    assert worker.reclaim_processing() == 0


def test_main_clears_last_error_on_success(meeting_home, flac, monkeypatch):
    """main() sukses (antrian habis) → worker.last_error dihapus: `meeting
    status` tak melaporkan 'API key invalid' lama setelah key diperbaiki."""
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())
    (paths.home() / "data" / "worker.last_error").write_text(
        "API key invalid", encoding="utf-8"
    )
    monkeypatch.setattr(worker, "transcribe_audio", FakeTranscriber())

    assert worker.main() == 0
    assert not (paths.home() / "data" / "worker.last_error").exists()
    assert list(paths.queue_dir("done").glob("*.json"))  # job memang diproses


def _seed_recorded_wav(job_id):
    import numpy as np
    import soundfile as sf
    from meeting import paths

    paths.ensure_dirs()
    wav = paths.tmp_dir() / f"{job_id}_mic.wav"
    t = np.linspace(0, 0.3, 4800, endpoint=False)
    sf.write(wav, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000, subtype="PCM_16")
    return wav


def test_aac_opt_in_finalizes_to_m4a(meeting_home, monkeypatch):
    from meeting import config, media, paths

    job_id = "aac1"
    wav = _seed_recorded_wav(job_id)
    config.update(audio_codec="aac", container="auto")
    calls = []

    def fake_encode(src, dst, channels=1, **kw):
        calls.append((str(src), str(dst), channels))
        dst.write_bytes(b"m4a")
        return dst

    monkeypatch.setattr(media, "encode_aac", fake_encode)
    monkeypatch.setattr(media, "verify_audio", lambda *a, **kw: True)
    monkeypatch.setattr(worker, "transcribe_audio", FakeTranscriber())
    queuer.enqueue(make_job(id=job_id, wav=f"tmp/{wav.name}", needs_finalization=True))
    assert worker.run_once() is True

    done = json.loads((paths.queue_dir("done") / f"{job_id}.json").read_text())
    assert done["audio"] == f"recordings/{job_id}.m4a"
    assert done["codec"] == "aac" and done["vbr"] is True
    assert done["bitrate"] == 64000
    assert calls and calls[0][1].endswith(".m4a")
    assert not wav.exists()


def test_aac_opt_in_falls_back_to_flac_without_ffmpeg(meeting_home, monkeypatch):
    from meeting import config, media, paths

    job_id = "aac2"
    wav = _seed_recorded_wav(job_id)
    config.update(audio_codec="aac")
    monkeypatch.setattr(media, "encode_aac", lambda *a, **kw: (_ for _ in ()).throw(media.MediaError("no ffmpeg")))
    monkeypatch.setattr(worker, "transcribe_audio", FakeTranscriber())
    queuer.enqueue(make_job(id=job_id, wav=f"tmp/{wav.name}", needs_finalization=True))
    assert worker.run_once() is True

    done = json.loads((paths.queue_dir("done") / f"{job_id}.json").read_text())
    assert done["audio"] == f"recordings/{job_id}.flac"
    assert "codec" not in done
    assert (paths.recordings_dir() / f"{job_id}.flac").exists()
