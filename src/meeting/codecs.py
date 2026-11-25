"""Abstraksi codec audio: encode via ffmpeg, probe via soundfile/ffprobe, resolve mime.
Modul ini belum dipakai caller mana pun — landasan refactor murni."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_MIME = {
    ".wav": "audio/wav", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".opus": "audio/ogg",
    ".mp4": "audio/mp4", ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg", ".webm": "audio/webm",
}
_SOUNDFILE_EXTS = {".wav", ".flac"}

# (mono, stereo) kbps — encoder Opus VBR default = balanced (~32/~64 kbps).
OPUS_BITRATES = {
    "best": (64, 128),
    "balanced": (32, 64),
    "compact": (24, 48),
}

CPU_THREADS = 2
_CPU_ENV = {
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
}


def cpu_env() -> dict[str, str]:
    """Environment lengkap dgn thread BLAS/OMP di-1 — CPU-only, tanpa GPU."""
    env = os.environ.copy()
    env.update(_CPU_ENV)
    return env


class CodecError(RuntimeError):
    pass


def ffmpeg_bin() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


def resolve_mime(path: Path | str) -> str:
    """Mime upload Gemini dari ekstensi; unknown → octet-stream (bukan tebak video)."""
    return _MIME.get(Path(path).suffix.lower(), "application/octet-stream")


def probe_duration(path: Path) -> float | None:
    """Durasi detik. soundfile dulu (murah) untuk WAV/FLAC, ffprobe untuk sisanya."""
    if Path(path).suffix.lower() in _SOUNDFILE_EXTS:
        try:
            import soundfile as sf

            info = sf.info(str(path))
            return info.frames / info.samplerate
        except Exception:
            pass
    ffprobe = ffprobe_bin()
    if not ffprobe:
        return None
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip()) if r.returncode == 0 else None
    except Exception:
        return None


def opus_bitrate(quality: str, channels: int) -> int:
    """kbps untuk profile + jumlah kanal; profile tak dikenal → balanced."""
    rates = OPUS_BITRATES.get(quality, OPUS_BITRATES["balanced"])
    return rates[1] if channels >= 2 else rates[0]


def build_opus_argv(
    ffmpeg: str,
    src: Path,
    out: Path,
    *,
    bitrate_kbps: int,
    channels: int,
    samplerate: int = 16000,
    threads: int = CPU_THREADS,
) -> list[str]:
    """Argv ffmpeg: input file dibaca langsung (streaming, runtime memory bounded).

    VBR + application voip = prioritas ucapan; compression_level 10 = kualitas
    terbaik di libopus. -hwaccel none + -threads 2 = CPU-only eksplisit."""
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
        "-hwaccel", "none", "-i", str(src),
        "-map", "0:a:0", "-vn", "-c:a", "libopus",
        "-vbr", "on", "-application", "voip", "-compression_level", "10",
        "-b:a", f"{bitrate_kbps}k", "-ac", str(channels), "-ar", str(samplerate),
        "-threads", str(threads), str(out),
    ]


def encode_opus(
    src: Path,
    out: Path,
    *,
    bitrate_kbps: int,
    channels: int,
    samplerate: int = 16000,
) -> Path:
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        raise CodecError(
            "ffmpeg tidak ditemukan — install dengan: winget install ffmpeg"
        )
    argv = build_opus_argv(
        ffmpeg, src, out,
        bitrate_kbps=bitrate_kbps, channels=channels, samplerate=samplerate,
    )
    r = subprocess.run(argv, capture_output=True, env=cpu_env())
    if r.returncode != 0:
        raise CodecError(f"ffmpeg gagal meng-encode Opus: {Path(src).name}")
    if not Path(out).exists():
        raise CodecError(f"ffmpeg tidak menghasilkan output: {Path(out).name}")
    return Path(out)


def decode_ok(path: Path, *, timeout: int = 120) -> bool:
    """Decode penuh ke /dev/null — verify bahwa file benar-benar bisa dibaca."""
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        return False
    argv = [
        ffmpeg, "-hide_banner", "-v", "error", "-hwaccel", "none", "-i", str(path),
        "-threads", str(CPU_THREADS), "-f", "null", "-",
    ]
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, env=cpu_env())
    except Exception:
        return False
    return r.returncode == 0
