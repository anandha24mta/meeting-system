"""Tulis job ke pending/ + spawn worker detached. FIFO = urutan nama file (created_at prefix)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import config, paths

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_OWNER_STALE_SEC = 900.0


def is_valid_id(s: str) -> bool:
    return bool(s) and bool(_ID_RE.match(s))


def enqueue(job: dict) -> Path:
    """Tulis job JSON atomik ke pending/. ID menjadi nama file.

    Dua job di menit yang sama (stop A, langsung record B) akan beradu nama —
    os.replace diam-diam menimpa A dan WAV-nya terdampar. Bila dest sudah ada,
    append -2, -3, … ke nama pertama yang kosong; field id job ikut disuffix
    agar JSON dan nama file selalu sepakat."""
    if not is_valid_id(job.get("id", "")):
        raise ValueError(f"id job ilegal: {job.get('id')!r} (hanya [A-Za-z0-9_-])")
    paths.ensure_dirs()
    stem = job["id"]
    n = 2
    while (paths.queue_dir("pending") / f"{stem}.json").exists():
        stem = f"{job['id']}-{n}"
        n += 1
    job = dict(job, id=stem)
    dest = paths.queue_dir("pending") / f"{stem}.json"
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(job, f, indent=2, ensure_ascii=False)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dest


def active_workers() -> int:
    """Jumlah worker yang punya kepemilikan job masih segar."""
    count = 0
    for owner in paths.queue_dir("pending").glob("*.processing.owner"):
        try:
            data = json.loads(owner.read_text(encoding="utf-8"))
            if time.time() - float(data["ts"]) < _OWNER_STALE_SEC:
                count += 1
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return count


def spawn_worker(max_workers: int | None = None) -> None:
    """Spawn worker detached sampai batas konkurensi; sisanya habiskan antrean sendiri."""
    limit = max(1, int(max_workers if max_workers is not None else config.load().get("max_workers", 4)))
    active = active_workers()
    pending = len(list(paths.queue_dir("pending").glob("*.json")))
    to_spawn = max(0, min(limit, max(1, pending)) - active)
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    for _ in range(to_spawn):
        try:
            subprocess.Popen(
                [sys.executable, "-m", "meeting.worker"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                **kwargs,
            )
        except OSError:
            # Worker tidak ter-spawn bukan error fatal job — user dapat jalankan manual.
            pass


def new_job_id(name: str | None = None) -> str:
    """ID default: tanggal-jam; --name di-append bila diberikan."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    return f"{stamp}-{name}" if name else stamp
