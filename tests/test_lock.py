import threading
import time

import pytest

from meeting import indexmd, lock, paths


def row(i):
    return {
        "id": f"job-{i}",
        "tanggal": "2026-08-29 14:00",
        "durasi": "0:30",
        "status": "done",
        "path": f"data/output/job-{i}/transcript.md",
    }


def test_index_upsert_is_serialized_across_threads(meeting_home):
    paths.ensure_dirs()
    errors = []

    def worker(i):
        try:
            indexmd.upsert(row(i))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors
    assert len(indexmd.rows()) == 24
    assert {r["id"] for r in indexmd.rows()} == {f"job-{i}" for i in range(24)}


def test_file_lock_releases_on_exception(meeting_home):
    guard = lock.FileLock(paths.home() / "data" / "test.lock")
    with pytest.raises(ValueError):
        with guard:
            raise ValueError("boom")
    with lock.FileLock(paths.home() / "data" / "test.lock"):
        pass


def test_file_lock_reclaims_stale_owner(meeting_home):
    import os

    path = paths.home() / "data" / "stale.lock"
    path.mkdir(parents=True, exist_ok=True)
    old = time.time() - 1000
    os.utime(path, (old, old))
    with lock.FileLock(path, timeout=30):
        assert path.exists()
    assert not path.exists()
