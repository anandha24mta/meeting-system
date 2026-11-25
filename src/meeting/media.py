"""ffmpeg-backed audio/container helpers. ffmpeg opsional; FLAC tetap default."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

AUDIO_MIME = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".mkv": "audio/x-matroska",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
}

VIDEO_MIME = {".mkv": "video/x-matroska"}

CONTAINERS = ("auto", "m4a", "mp4", "mkv")
VIDEO_SOURCE_EXTS = (".mp4", ".mkv")
NATIVE_Q = {1: "0.5", 2: "1.0"}
FDK_VBR = {1: "3", 2: "4"}
BITRATE_TARGET = {1: 64000, 2: 128000}


class MediaError(RuntimeError):
    pass


def wants_aac(cfg: dict) -> bool:
    return str(cfg.get("audio_codec", "flac")).lower() == "aac"


def container_for(cfg: dict, *, source_ext: str | None = None) -> str:
    """auto: m4a untuk rekaman audio-only, container sumber untuk file audio/video."""
    choice = str(cfg.get("container", "auto")).lower().lstrip(".")
    if choice not in CONTAINERS:
        raise MediaError(f"container tidak dikenal: {choice!r} (auto/m4a/mp4/mkv)")
    if choice != "auto":
        return choice
    if source_ext and source_ext.lower() in (".m4a", ".mp4", ".mkv"):
        return source_ext.lower().lstrip(".")
    return "m4a"


def audio_mime(path: Path | str) -> str | None:
    return AUDIO_MIME.get(Path(path).suffix.lower())


def container_mime(path: Path | str, has_video: bool) -> str | None:
    """Mime untuk upload: audio bila tak ada video; video hanya bila eksplisit."""
    ext = Path(path).suffix.lower()
    return VIDEO_MIME.get(ext) if has_video else AUDIO_MIME.get(ext)


def probe_encoders(ffmpeg: str | None = None) -> set[str]:
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        return set()
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30
        )
    except Exception:
        return set()
    encoders = set()
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0][:1] in ("A", "V", "S"):
            encoders.add(parts[1])
    return encoders


def aac_vbr_args(channels: int = 1, encoders: set[str] | None = None) -> list[str]:
    ch = 2 if channels > 1 else 1
    if encoders is not None and "libfdk_aac" in encoders:
        return ["-c:a", "libfdk_aac", "-profile:a", "aac_low", "-vbr", FDK_VBR[ch]]
    return ["-c:a", "aac", "-profile:a", "aac_low", "-q:a", NATIVE_Q[ch]]


def aac_bitrate(channels: int = 1) -> int:
    return BITRATE_TARGET[2 if channels > 1 else 1]


def aac_output_argv(
    src: Path | str, dst: Path | str, channels: int = 1,
    encoders: set[str] | None = None, sample_rate: int = 16000,
) -> list[str]:
    return [
        "-y", "-i", str(src), "-vn", *aac_vbr_args(channels, encoders),
        "-ac", str(channels), "-ar", str(sample_rate), str(dst),
    ]


def passthrough_argv(
    src: Path | str, dst: Path | str, *, channels: int = 1,
    encoders: set[str] | None = None, video: bool = True,
) -> list[str]:
    """Video stream copy (byte-identical, VFR utuh) atau ekstrak audio saja."""
    args = ["-y", "-i", str(src)]
    if video:
        args += ["-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "copy"]
    else:
        args += ["-vn"]
    args += [*aac_vbr_args(channels, encoders), "-ac", str(channels), str(dst)]
    return args


def probe_video(path: Path | str, *, ffprobe: str | None = None, run=None) -> bool:
    ffprobe = ffprobe or shutil.which("ffprobe")
    if not ffprobe:
        return False
    run = run or subprocess.run
    try:
        out = run(
            [ffprobe, "-v", "error", "-select_streams", "v", "-show_entries",
             "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return False
    return bool(out.stdout.strip())


def encode_aac(
    src: Path | str, dst: Path | str, channels: int = 1,
    *, ffmpeg: str | None = None, encoders: set[str] | None = None,
) -> Path:
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        raise MediaError("ffmpeg tidak ditemukan — install dengan: winget install ffmpeg")
    if encoders is None:
        encoders = probe_encoders(ffmpeg)
    out = Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    rc = subprocess.run(
        [ffmpeg, *aac_output_argv(src, out, channels, encoders)], capture_output=True
    )
    if rc.returncode != 0 or not out.exists():
        raise MediaError(f"ffmpeg gagal membuat AAC: {out.name}")
    return out
