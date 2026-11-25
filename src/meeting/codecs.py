"""Abstraksi codec audio: encode via ffmpeg, probe via soundfile/ffprobe, resolve mime.
Modul ini belum dipakai caller mana pun — landasan refactor murni."""
from __future__ import annotations

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


def build_opus_argv(
    ffmpeg: str,
    src: Path,
    out: Path,
    *,
    bitrate_kbps: int,
    channels: int,
    samplerate: int = 16000,
) -> list[str]:
    """Argv ffmpeg: input file dibaca langsung (streaming, runtime memory bounded)."""
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-map", "0:a:0", "-vn", "-c:a", "libopus",
        "-b:a", f"{bitrate_kbps}k", "-ac", str(channels), "-ar", str(samplerate),
        str(out),
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
    r = subprocess.run(argv, capture_output=True)
    if r.returncode != 0:
        raise CodecError(f"ffmpeg gagal meng-encode Opus: {Path(src).name}")
    if not out.exists():
        raise CodecError(f"ffmpeg tidak menghasilkan output: {out.name}")
    return out
