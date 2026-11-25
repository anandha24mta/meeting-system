"""CPU/RAM-only planning. GPU capability lives in hwaccel.py and is never assumed."""
from __future__ import annotations

import os
import sys

from . import config


def cpu_count() -> int:
    return max(1, os.cpu_count() or 2)


def ram_total_mb() -> float:
    if sys.platform == "win32":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullTotalPhys / (1024 * 1024)
        except Exception:
            pass
        return 512.0
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return pages * page_size / (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        return 512.0


def plan(cfg: dict | None = None, cpu: int | None = None, ram_mb: float | None = None) -> dict:
    """Bounded burst: never exceed CPUs/2, RAM budget, or configured cap."""
    cfg = cfg if cfg is not None else config.load()
    cpu = cpu if cpu is not None else cpu_count()
    ram = ram_mb if ram_mb is not None else ram_total_mb()
    per_job = max(1.0, float(cfg.get("per_job_memory_mb", 256)))
    by_cpu = max(1, cpu // 2)
    by_ram = max(1, int(ram // per_job))
    workers = max(1, min(int(cfg.get("max_workers", 4)), by_cpu, by_ram))
    return {
        "max_workers": workers,
        "max_parallel_chunks": max(1, int(cfg.get("max_parallel_chunks", 2))),
        "thread_cap": max(1, int(cfg.get("thread_cap", 2))),
        "cpu_count": cpu,
        "ram_total_mb": ram,
    }


def apply_thread_limits(plan_info: dict | None = None) -> None:
    """Pin BLAS/OMP threads so parallel jobs do not oversubscribe CPUs."""
    plan_info = plan_info or plan()
    value = str(plan_info["thread_cap"])
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, value)
