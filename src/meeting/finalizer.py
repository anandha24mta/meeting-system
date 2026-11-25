"""WAV → FLAC 16 kHz (default/archive) atau Opus VBR cpu-only (codec=opus).
WAV dihapus hanya setelah output diverify terbaca — audio aman dulu, bersih belakangan."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from . import codecs, config, paths


class FinalizeError(RuntimeError):
    pass


def _note(msg: str) -> None:
    """Pemberitahuan fallback — stderr, bukan log API key."""
    import sys

    print(f"[finalizer] {msg}", file=sys.stderr)


def _read_16k_mono(wav: Path) -> np.ndarray:
    data, sr = sf.read(wav, dtype="float32", always_2d=False)
    if data.ndim > 1:  # WAV multi-kanal unexpected → mixdown
        data = data.mean(axis=1)
    if sr != 16000:
        raise FinalizeError(f"{wav.name}: samplerate {sr}, harap 16000 (recorder selalu menulis 16k)")
    return data.astype(np.float32)


def _uses_opus(cfg: dict) -> bool:
    """Opus hanya saat config MEMILIH codec=opus + quality lossy; default tetap FLAC."""
    return cfg.get("codec") == "opus" and cfg.get("quality") in codecs.OPUS_BITRATES


def _merge_to_temp_wav(wavs: list[Path], out: Path, chunk: int = 1 << 16) -> None:
    """Gabung mono → WAV stereo per blok: memory bounded, tanpa array sepanjang file."""
    out.parent.mkdir(parents=True, exist_ok=True)
    readers = []
    try:
        for w in wavs:
            readers.append(sf.SoundFile(str(w), "r"))
        sr = readers[0].samplerate
        with sf.SoundFile(str(out), "w", samplerate=sr, channels=len(readers),
                          subtype="PCM_16") as dst:
            while True:
                blocks = [r.read(chunk, dtype="float32") for r in readers]
                if not any(len(b) for b in blocks):
                    break
                n = max(len(b) for b in blocks)
                merged = np.zeros((n, len(blocks)), dtype=np.float32)
                for i, b in enumerate(blocks):
                    if b.size == 0:
                        continue
                    if b.ndim > 1:  # input multi-kanal → mixdown ke mono
                        b = b.mean(axis=1)
                    merged[: len(b), i] = b
                dst.write(merged)
    finally:
        for r in readers:
            r.close()


def _finalize_flac(wav_paths: list[Path], out_stem: str, cfg: dict) -> Path:
    out_path = paths.recordings_dir() / f"{out_stem}.flac"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    channels = [_read_16k_mono(w) for w in wav_paths]
    if len(channels) == 1:
        merged = channels[0]
    else:
        n = max(len(c) for c in channels)
        merged = np.zeros((n, len(channels)), dtype=np.float32)
        for i, c in enumerate(channels):
            merged[: len(c), i] = c

    sf.write(out_path, merged, 16000, format="FLAC")

    info = sf.info(out_path)
    if info.frames == 0 or info.samplerate != 16000:
        out_path.unlink(missing_ok=True)
        raise FinalizeError(f"FLAC gagal verify: {out_path.name}")

    for w in wav_paths:
        w.unlink(missing_ok=True)
    return out_path


def _finalize_opus(wav_paths: list[Path], out_stem: str, cfg: dict) -> Path:
    quality = cfg["quality"]
    cfg_channels = cfg.get("channels", "auto")
    channels = cfg_channels if isinstance(cfg_channels, int) and cfg_channels in (1, 2) \
        else (1 if len(wav_paths) == 1 else 2)
    samplerate = int(cfg.get("sample_rate") or 16000)
    bitrate = codecs.opus_bitrate(quality, channels)

    out_path = paths.recordings_dir() / f"{out_stem}.opus"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    temp_wav: Path | None = None
    reason: str | None = None
    try:
        if len(wav_paths) == 1 and channels == 1:
            src = wav_paths[0]  # ffmpeg baca file langsung — tidak ada array numpy penuh
        else:
            temp_wav = paths.tmp_dir() / f"{out_stem}_merged.wav"
            _merge_to_temp_wav(wav_paths, temp_wav)
            src = temp_wav

        codecs.encode_opus(
            src, out_path,
            bitrate_kbps=bitrate, channels=channels, samplerate=samplerate,
        )

        # verify durasi/frame + decode penuh SEBELUM hapus WAV
        src_duration = max((codecs.probe_duration(w) or 0.0) for w in wav_paths)
        duration = codecs.probe_duration(out_path)
        drift = abs(duration - src_duration) if duration and src_duration else 0.0
        if (
            not duration or duration <= 0
            or not codecs.decode_ok(out_path)
            or drift > max(1.0, src_duration * 0.1)
        ):
            raise FinalizeError(f"Opus gagal verify: {out_path.name}")
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
    finally:
        if temp_wav is not None:
            temp_wav.unlink(missing_ok=True)

    if reason is not None:
        out_path.unlink(missing_ok=True)  # output cacat tak boleh dikirim ke Gemini
        _note(f"Opus gagal ({reason}) — fallback ke FLAC (ffmpeg opsional)")
        return _finalize_flac(wav_paths, out_stem, cfg)

    for w in wav_paths:
        w.unlink(missing_ok=True)
    return out_path


def finalize(wav_paths: list[Path], out_stem: str, cfg: dict | None = None) -> Path:
    if not wav_paths:
        raise FinalizeError("tidak ada WAV input")
    for w in wav_paths:
        if not w.exists():
            raise FinalizeError(f"WAV hilang: {w}")

    if cfg is None:
        cfg = config.load()
    if _uses_opus(cfg):
        return _finalize_opus(wav_paths, out_stem, cfg)
    return _finalize_flac(wav_paths, out_stem, cfg)


def resolve_audio(job: dict) -> Path:
    """Path audio utk worker: WAV utk needs_finalization, file final utk yang sudah final."""
    rel = job["wav"] if job.get("needs_finalization") else job["audio"]
    p = Path(rel)
    return p if p.is_absolute() else paths.home() / "data" / rel
