"""Deteksi & pemulihan sesi yatim (crash/force-quit): WAV utuh → tawarkan finalisasi
atau simpan ke discarded/ (tidak pernah hapus diam-diam)."""
from __future__ import annotations

import json
import shutil

from . import finalizer, indexmd, paths, queuer, session


def find_orphans() -> list[dict]:
    """Sesi yatim = session file dgn PID mati. WAV dicocokkan dari session file."""
    orphan = session.orphaned()
    if orphan:
        return [orphan]
    return []


def offer_recovery(ui_confirm) -> dict | None:
    orphans = find_orphans()
    if not orphans:
        return None
    meta = orphans[0]
    durasi = indexmd.fmt_duration(_frames(meta) / 16000)
    msg = (
        f"Ditemukan rekaman yatim dari sesi yang tidak selesai (id {meta['id']}, "
        f"mulai {meta.get('started_at')}, ≈{durasi}). Finalisasi & transkripsi sekarang? "
        f"[y/N]"
    )
    if not ui_confirm(msg):
        # tolak → pindahkan ke discarded/, JANGAN hapus
        disc = paths.tmp_dir() / "discarded"
        disc.mkdir(parents=True, exist_ok=True)
        for w in _wav_paths(meta):
            if w.exists():
                shutil.move(str(w), disc / w.name)
        paths.session_file().unlink(missing_ok=True)
        return None

    wavs = [w for w in _wav_paths(meta) if w.exists()]
    flac = finalizer.finalize(wavs, meta["id"])
    job = {
        "id": meta["id"],
        "created_at": meta.get("started_at") or "unknown",
        "source": meta.get("source", "mic"),
        "audio": str(flac.relative_to(paths.home() / "data")),
        "wav": None,
        "original_path": None,
        "needs_finalization": False,
        "duration_sec": _frames(meta) / 16000,
        "device": meta.get("device"),
        "model": _current_model(),
        "status": "pending",
    }
    queuer.enqueue(job)
    indexmd.upsert(
        {
            "id": job["id"],
            "tanggal": (job["created_at"] or "").replace("T", " "),
            "durasi": indexmd.fmt_duration(job["duration_sec"]),
            "status": "pending",
            "path": f"data/output/{job['id']}/transcript.md",
        }
    )
    paths.session_file().unlink(missing_ok=True)
    queuer.spawn_worker()
    return job


def _wav_paths(meta: dict) -> list:
    from pathlib import Path as _P

    out = []
    for w in meta.get("wav_paths", []):
        p = _P(w)
        out.append(p if p.is_absolute() else paths.home() / "data" / w)
    return out


def _frames(meta: dict) -> int:
    import soundfile as sf

    total = 0
    for w in _wav_paths(meta):
        if w.exists():
            try:
                total = max(total, sf.info(w).frames)
            except Exception:
                pass
    return total


def _current_model() -> str:
    from . import config

    return config.load().get("model", "gemini-flash")
