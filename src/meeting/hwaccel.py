"""Lazy hardware capability probe. GPU libraries are never imported here."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import paths

CACHE_TTL_SEC = 86400.0
_KIND_BY_VENDOR = (("nvidia", "nvenc"), ("intel", "qsv"), ("amd", "vcn"), ("radeon", "vcn"))


def _cache_path() -> Path:
    return paths.home() / "data" / "hwaccel.json"


def _windows_devices() -> list[str]:
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _nvidia_devices() -> list[str]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.stdout.splitlines() if "GPU" in line]


def _classify(devices: list[str]) -> str:
    names = " ".join(devices).lower()
    for vendor, kind in _KIND_BY_VENDOR:
        if vendor in names:
            return kind
    return "none"


def _probe() -> dict:
    devices = _nvidia_devices() + _windows_devices()
    kind = _classify(devices)
    opencl_installed = importlib.util.find_spec("pyopencl") is not None
    return {
        "kind": kind,
        "available": kind != "none" or opencl_installed,
        "opencl_installed": opencl_installed,
        "devices": devices,
        "checked_at": time.time(),
    }


def probe(force: bool = False) -> dict:
    """Cached capability dict. Never imports CUDA/OpenCL/DirectML modules."""
    from . import config

    if config.load().get("gpu_mode", "auto") == "off":
        return {"kind": "none", "available": False, "opencl_installed": False, "devices": []}
    cache = _cache_path()
    if not force and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if time.time() - float(data.get("checked_at", 0)) < CACHE_TTL_SEC:
                return data
        except (OSError, ValueError, TypeError):
            pass
    data = _probe()
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, cache)
    return data
