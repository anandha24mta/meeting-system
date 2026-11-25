"""Atomic cross-process lock for shared files (INDEX.md, worker error state).

Directory creation is the atomic acquire primitive: a file created with O_CREAT
has a brief window where it is empty and can be mistaken for a stale lock.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path


class FileLock:
    def __init__(self, path: Path, timeout: float = 30.0, poll: float = 0.02) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self.poll = poll
        self._held = False

    def acquire(self) -> "FileLock":
        deadline = time.monotonic() + self.timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                os.mkdir(str(self.path))
            except FileExistsError:
                if self._stale():
                    shutil.rmtree(self.path, ignore_errors=True)
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"lock tidak bisa diambil: {self.path}")
                time.sleep(self.poll)
                continue
            (self.path / "owner").write_text(str(os.getpid()), encoding="utf-8")
            self._held = True
            return self

    def _stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > self.timeout
        except OSError:
            return True

    def release(self) -> None:
        if not self._held:
            return
        shutil.rmtree(self.path, ignore_errors=True)
        self._held = False

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
