"""WAV → FLAC 16 kHz. Mono untuk mic/sys; stereo (mic=kiri, sys=kanan) untuk --both.
WAV dihapus hanya setelah FLAC diverify terbaca — prinsip: audio aman dulu, bersih-bersih belakangan."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from . import paths


class FinalizeError(RuntimeError):
    pass


def _read_16k_mono(wav: Path) -> np.ndarray:
    data, sr = sf.read(wav, dtype="float32", always_2d=False)
    if data.ndim > 1:  # WAV multi-kanal unexpected → mixdown
        data = data.mean(axis=1)
    if sr != 16000:
        raise FinalizeError(f"{wav.name}: samplerate {sr}, harap 16000 (recorder selalu menulis 16k)")
    return data.astype(np.float32)


def finalize(wav_paths: list[Path], out_stem: str) -> Path:
    if not wav_paths:
        raise FinalizeError("tidak ada WAV input")
    for w in wav_paths:
        if not w.exists():
            raise FinalizeError(f"WAV hilang: {w}")

    out_path = paths.recordings_dir() / f"{out_stem}.flac"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    channels = [_read_16k_mono(w) for w in wav_paths]
    if len(channels) == 1:
        merged = channels[0]
    else:
        # pad kanal pendek dengan silence agar panjang sama
        n = max(len(c) for c in channels)
        merged = np.zeros((n, len(channels)), dtype=np.float32)
        for i, c in enumerate(channels):
            merged[: len(c), i] = c

    sf.write(out_path, merged, 16000, format="FLAC")

    # verify sebelum hapus WAV — FLAC korup tidak boleh menghapus sumber
    info = sf.info(out_path)
    if info.frames == 0 or info.samplerate != 16000:
        out_path.unlink(missing_ok=True)
        raise FinalizeError(f"FLAC gagal verify: {out_path.name}")

    for w in wav_paths:
        w.unlink(missing_ok=True)
    return out_path


def resolve_audio(job: dict) -> Path:
    """Path audio utk worker: WAV utk needs_finalization, FLAC utk yang sudah final.
    Relatif diresolve thd home()/data/; absolut (source=="file") dipakai apa adanya."""
    rel = job["wav"] if job.get("needs_finalization") else job["audio"]
    p = Path(rel)
    return p if p.is_absolute() else paths.home() / "data" / rel
