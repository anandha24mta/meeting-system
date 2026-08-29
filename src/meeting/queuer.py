"""Tulis job ke pending/ + spawn worker detached. FIFO = urutan nama file (created_at prefix)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import paths

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def is_valid_id(s: str) -> bool:
    return bool(s) and bool(_ID_RE.match(s))


def enqueue(job: dict) -> Path:
    """Tulis job JSON atomik ke pending/. ID menjadi nama file."""
    if not is_valid_id(job.get("id", "")):
        raise ValueError(f"id job ilegal: {job.get('id')!r} (hanya [A-Za-z0-9_-])")
    paths.ensure_dirs()
    dest = paths.queue_dir("pending") / f"{job['id']}.json"
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(job, f, indent=2, ensure_ascii=False)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dest


def spawn_worker() -> None:
    """Jalankan meeting-worker detached. Best-effort: worker juga bisa dipanggil manual."""
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
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
