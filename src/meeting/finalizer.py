"""WAV → FLAC 16 kHz. Mono untuk mic/sys; stereo (mic=kiri, sys=kanan) untuk --both.
WAV dihapus hanya setelah FLAC diverify terbaca — prinsip: audio aman dulu, bersih-bersih belakangan."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from . import paths


class FinalizeError(RuntimeError):
    pass


BLOCK_FRAMES = 8192


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

    readers: list[sf.SoundFile] = []
    writer: sf.SoundFile | None = None
    try:
        for wav in wav_paths:
            reader = sf.SoundFile(str(wav), mode="r")
            if reader.samplerate != 16000:
                raise FinalizeError(
                    f"{wav.name}: samplerate {reader.samplerate}, harap 16000 (recorder selalu menulis 16k)"
                )
            readers.append(reader)
        writer = sf.SoundFile(
            str(out_path), mode="w", samplerate=16000,
            channels=len(readers), subtype="PCM_16", format="FLAC",
        )
        while True:
            blocks = [reader.read(BLOCK_FRAMES, dtype="float32") for reader in readers]
            if not any(len(block) for block in blocks):
                break
            n = max(len(block) for block in blocks)
            merged = np.zeros((n, len(readers)), dtype=np.float32)
            for i, block in enumerate(blocks):
                if block.ndim > 1:
                    block = block.mean(axis=1)
                merged[: len(block), i] = block
            writer.write(merged)
    except Exception:
        if writer is not None:
            writer.close()
        out_path.unlink(missing_ok=True)
        raise
    finally:
        for reader in readers:
            reader.close()
        if writer is not None:
            writer.close()

    # verify sebelum hapus WAV — FLAC korup tidak boleh menghapus sumber
    info = sf.info(out_path)
    if info.frames == 0 or info.samplerate != 16000 or info.channels != len(wav_paths):
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
