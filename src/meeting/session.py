"""Session-file lock: satu sesi rekam aktif lintas proses (CLI + floating button).
PID mati = yatim → bukan aktif; file dibiarkan utk recovery (recover.py)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from . import paths


class SessionActiveError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) di Windows = CTRL_C_EVENT (bukan probe!) — pakai Win32 API.
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _read() -> dict | None:
    p = paths.session_file()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def active() -> dict | None:
    data = _read()
    if data and _pid_alive(int(data.get("pid", 0))):
        return data
    return None


def orphaned() -> dict | None:
    """Sesi yatim: file ada, PID mati."""
    data = _read()
    if data and not _pid_alive(int(data.get("pid", 0))):
        return data
    return None


def start(meta: dict) -> None:
    act = active()
    if act:
        raise SessionActiveError(
            f"Masih ada sesi rekam aktif (id {act.get('id')}, mulai "
            f"{act.get('started_at')}). Hentikan dulu dengan meeting stop "
            f"/ klik tombol stop."
        )
    data = dict(meta)
    data["pid"] = os.getpid()
    data["started_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    p = paths.session_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    clear_stop_flag()


def request_stop() -> None:
    paths.ensure_dirs()
    paths.stop_flag().touch()


def stop_requested() -> bool:
    return paths.stop_flag().exists()


def clear_stop_flag() -> None:
    paths.stop_flag().unlink(missing_ok=True)


def finish() -> dict:
    data = _read()
    if data is None:
        raise SessionActiveError("tidak ada sesi rekam untuk diselesaikan")
    paths.session_file().unlink(missing_ok=True)
    clear_stop_flag()
    return data
