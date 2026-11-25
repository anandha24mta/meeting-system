"""Deteksi & pemulihan sesi yatim (crash/force-quit): WAV utuh → tawarkan finalisasi
atau simpan ke discarded/ (tidak pernah hapus diam-diam).

WAV kandidat TIDAK diambil dari meta["wav_paths"] saja — producer (cli.cmd_record,
button) selalu menulis `wav_paths: []` karena file session tidak pernah di-update
selama rekaman. Kandidat diglob dari tmp/ (id_mic/id_sys) lalu di-union dgn
wav_paths agar shape lama tetap jalan. Jalur confirmed failure-safe: gagal pun
session file dihapus & WAV dipindah ke discarded/ — tool tidak pernah wedged."""
from __future__ import annotations

import shutil
from pathlib import Path

from . import finalizer, indexmd, paths, queuer, session


def find_orphans() -> list[dict]:
    """Sesi yatim = session file dgn PID mati. WAV dicocokkan saat recovery."""
    orphan = session.orphaned()
    if orphan:
        return [orphan]
    return []


def offer_recovery(ui_confirm) -> dict | None:
    orphans = find_orphans()
    if not orphans:
        return None
    meta = orphans[0]
    cands = _wav_paths(meta)
    dur = _frames(cands) / 16000  # hitung SEBELUM finalize — finalize menghapus WAV
    durasi = indexmd.fmt_duration(dur)
    msg = (
        f"Ditemukan rekaman yatim dari sesi yang tidak selesai (id {meta['id']}, "
        f"mulai {meta.get('started_at')}, ≈{durasi}). Finalisasi & transkripsi sekarang? "
        f"[y/N]"
    )
    if not ui_confirm(msg):
        # tolak → pindahkan ke discarded/, JANGAN hapus
        _discard(cands)
        paths.session_file().unlink(missing_ok=True)
        return None

    wavs = [w for w in cands if w.exists()]
    try:
        if not wavs:
            raise finalizer.FinalizeError("tidak ada WAV input")
        flac = finalizer.finalize(wavs, meta["id"])
        job = {
            "id": meta["id"],
            "created_at": meta.get("started_at") or "unknown",
            "source": meta.get("source", "mic"),
            "audio": str(flac.relative_to(paths.home() / "data")),
            "wav": None,
            "original_path": None,
            "needs_finalization": False,
            "duration_sec": dur,
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
    except Exception as e:
        # gagal finalize/enqueue → JANGAN wedge: selamatkan WAV ke discarded/
        # (tidak pernah hapus), hapus session file, laporkan via return None.
        _discard([w for w in cands if w.exists()])
        paths.session_file().unlink(missing_ok=True)
        print(f"gagal memulihkan sesi yatim {meta['id']}: {e}", file=_stderr())
        return None
    paths.session_file().unlink(missing_ok=True)
    queuer.spawn_worker()
    return job


def _stderr():
    import sys

    return sys.stderr


def _discard(cands: list[Path]) -> None:
    """Pindahkan WAV kandidat ke tmp/discarded/ — tidak pernah hapus."""
    disc = paths.tmp_dir() / "discarded"
    disc.mkdir(parents=True, exist_ok=True)
    for w in cands:
        if w.exists():
            shutil.move(str(w), disc / w.name)


def _wav_paths(meta: dict) -> list[Path]:
    """Union: glob tmp/<id>_mic.wav & tmp/<id>_sys.wav + wav_paths dari session
    file (relatif diresolve thd home()/data/). Producer menulis `wav_paths: []`
    saat start dan tidak pernah meng-update file — glob adalah sumber utama."""
    out: list[Path] = []
    tmp = paths.tmp_dir()
    jid = str(meta.get("id", ""))
    for name in (f"{jid}_mic.wav", f"{jid}_sys.wav"):
        p = tmp / name
        if p.exists():
            out.append(p)
    for w in meta.get("wav_paths", []) or []:
        p = Path(w)
        p = p if p.is_absolute() else paths.home() / "data" / w
        if p not in out:
            out.append(p)
    return out


def _frames(cands: list[Path]) -> int:
    import soundfile as sf

    total = 0
    for w in cands:
        if w.exists():
            try:
                total = max(total, sf.info(w).frames)
            except Exception:
                pass
    return total


def _current_model() -> str:
    from . import config

    return config.load().get("model", "gemini-2.5-flash")
