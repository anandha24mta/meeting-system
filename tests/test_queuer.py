import json

import pytest

from meeting import queuer


def make_job(**over):
    job = {
        "id": "2026-08-29_1400-standup",
        "created_at": "2026-08-29T14:00:05",
        "source": "mic",
        "audio": "recordings/2026-08-29_1400-standup.flac",
        "wav": "tmp/2026-08-29_1400-standup_mic.wav",
        "original_path": None,
        "needs_finalization": True,
        "duration_sec": 3600.5,
        "device": "Headset (Jabra)",
        "model": "gemini-flash",
        "status": "pending",
    }
    job.update(over)
    return job


def test_enqueue_writes_job_json(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    p = queuer.enqueue(make_job())
    assert p == paths.queue_dir("pending") / "2026-08-29_1400-standup.json"
    assert json.loads(p.read_text(encoding="utf-8"))["id"] == "2026-08-29_1400-standup"


def test_enqueue_atomic_no_tmp_left(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())
    leftovers = [f for f in paths.queue_dir("pending").iterdir() if f.suffix == ".tmp"]
    assert leftovers == []


def test_enqueue_same_minute_collision_suffixed(meeting_home):
    """Dua job di menit yang sama tidak boleh saling menimpa: nama file AND
    field id keduanya disuffix (-2, -3, …) agar JSON/nama file sepakat."""
    from meeting import paths
    paths.ensure_dirs()
    p1 = queuer.enqueue(make_job())
    p2 = queuer.enqueue(make_job())
    p3 = queuer.enqueue(make_job())

    assert p1.name == "2026-08-29_1400-standup.json"
    assert p2.name == "2026-08-29_1400-standup-2.json"
    assert p3.name == "2026-08-29_1400-standup-3.json"
    # id di dalam JSON ikut disuffix — bukan hanya nama file
    assert json.loads(p2.read_text(encoding="utf-8"))["id"] == "2026-08-29_1400-standup-2"
    assert json.loads(p3.read_text(encoding="utf-8"))["id"] == "2026-08-29_1400-standup-3"
    # job pertama tidak tertimpa
    first = json.loads(p1.read_text(encoding="utf-8"))
    assert first["id"] == "2026-08-29_1400-standup"


def test_enqueue_rejects_bad_id(meeting_home):
    paths_ = queuer.paths
    paths_.ensure_dirs()
    with pytest.raises(ValueError):
        queuer.enqueue(make_job(id="../evil"))
    with pytest.raises(ValueError):
        queuer.enqueue(make_job(id="sp ace"))


def test_spawn_worker_popen_called(meeting_home, monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, *a, **kw):
            calls.append((a, kw))

    monkeypatch.setattr(queuer.subprocess, "Popen", FakePopen)
    queuer.spawn_worker()
    assert len(calls) == 1
    args, kw = calls[0]
    cmd = args[0]
    assert "meeting.worker" in cmd  # python -m meeting.worker
    assert kw["stdout"] == queuer.subprocess.DEVNULL  # detached: tak menempel di terminal
