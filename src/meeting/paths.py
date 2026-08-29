"""Semua path tool. Tunggal sumber kebenaran; MEETING_HOME meng-override root."""
from __future__ import annotations

import os
from pathlib import Path

_QUEUE_SUBS = ("pending", "done", "failed")


def project_root() -> Path:
    """Root repo/paket — dipakai untuk default home dan resolve resource."""
    return Path(__file__).resolve().parent.parent.parent


def home() -> Path:
    """Root data tool. Env MEETING_HOME override (testing / lokasi lain)."""
    env = os.environ.get("MEETING_HOME")
    return Path(env).resolve() if env else project_root()


def recordings_dir() -> Path:
    return home() / "data" / "recordings"


def queue_dir(sub: str) -> Path:
    if sub not in _QUEUE_SUBS:
        raise ValueError(f"sub must be one of {_QUEUE_SUBS}, got {sub!r}")
    return home() / "data" / "queue" / sub


def output_dir() -> Path:
    return home() / "data" / "output"


def tmp_dir() -> Path:
    return home() / "data" / "tmp"


def worker_log() -> Path:
    return home() / "data" / "worker.log"


def session_file() -> Path:
    return home() / "data" / "record_session.json"


def stop_flag() -> Path:
    return tmp_dir() / "record.stop"


def index_file() -> Path:
    return home() / "INDEX.md"


def config_file() -> Path:
    return home() / "config.json"


def env_file() -> Path:
    return home() / ".env"


def ensure_dirs() -> None:
    for d in (
        recordings_dir(),
        *(queue_dir(s) for s in _QUEUE_SUBS),
        output_dir(),
        tmp_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)
