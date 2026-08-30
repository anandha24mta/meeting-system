"""Listing & pemilihan device. Backend lazy — modul bisa diimport tanpa audio lib."""
from __future__ import annotations

from . import config


def list_devices() -> list[dict]:
    result: list[dict] = []
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            result.append({"kind": "mic", "name": d["name"], "index": i})

    import soundcard as sc

    for m in sc.all_microphones(include_loopback=True):
        if getattr(m, "isloopback", False):
            result.append({"kind": "loopback", "name": m.name, "index": None})
    return result


def set_mic(name: str | None) -> None:
    """None = ikut default Windows."""
    config.update(mic_device=name)
