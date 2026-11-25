"""Measure local resource use of the lightweight steps. No third-party deps."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from . import paths


def rss_mb() -> float:
    """Resident set size in MB; psutil used when present, platform API otherwise."""
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PMCOUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = PMCOUNTERS()
            counters.cb = ctypes.sizeof(PMCOUNTERS)
            kernel32 = ctypes.windll.kernel32
            process = kernel32.GetCurrentProcess()
            if kernel32.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
                return counters.WorkingSetSize / (1024 * 1024)
        except Exception:
            pass
        return 0.0
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value / 1024 if sys.platform != "darwin" else value / (1024 * 1024)
    except Exception:
        return 0.0


def _synthetic_wav(seconds: float) -> Path:
    import numpy as np
    import soundfile as sf

    paths.ensure_dirs()
    sr = 16000
    data = np.zeros(int(sr * seconds), dtype=np.float32)
    out = paths.tmp_dir() / "bench.wav"
    sf.write(str(out), data, sr, subtype="PCM_16")
    return out


def run(out: Path, workload: str = "finalize", seconds: float = 1.0) -> dict:
    """Run one local workload, write measurement JSON to `out`, return it."""
    wav = _synthetic_wav(max(seconds, 0.1))
    rss_before = rss_mb()
    cpu_before = time.process_time()
    started = time.monotonic()
    if workload == "finalize":
        from .finalizer import finalize

        finalize([wav], "bench-session")
    elif workload == "silence":
        from .transcribe import _is_silent

        _is_silent(wav)
    else:
        raise ValueError(f"unknown workload: {workload!r}")
    result = {
        "workload": workload,
        "seconds": round(time.monotonic() - started, 4),
        "cpu_seconds": round(time.process_time() - cpu_before, 4),
        "rss_before_mb": round(rss_before, 2),
        "rss_after_mb": round(rss_mb(), 2),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
