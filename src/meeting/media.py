"""ffmpeg-backed audio/container helpers. Default: WAV → AAC/m4a langsung.
FLAC dipakai bila ffmpeg absen atau `audio_codec: flac`. Tak ada video capture
hari ini — video hanya berasal dari file sumber dan di-copy."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import paths

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

CODEC_BY_EXT = {
    ".flac": "flac", ".wav": "pcm", ".m4a": "aac", ".mp4": "aac",
    ".mkv": "aac", ".mp3": "mp3", ".ogg": "vorbis", ".webm": "opus",
}

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


def guess_codec(path: Path | str) -> str | None:
    return CODEC_BY_EXT.get(Path(path).suffix.lower())


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


def crf_args(crf: int = 23) -> list[str]:
    return ["-crf", str(crf)]


def vfr_args() -> list[str]:
    return ["-fps_mode", "vfr"]


def probe_hwaccel(ffmpeg: str | None = None, run=None) -> str | None:
    """Probe malas: hanya dipanggil saat video akan di-re-encode."""
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    run = run or subprocess.run
    try:
        out = run(
            [ffmpeg, "-hide_banner", "-hwaccels"], capture_output=True, text=True, timeout=30
        )
    except Exception:
        return None
    available = {
        line.strip() for line in out.stdout.splitlines()
        if line.strip() and line.strip().rstrip(":") != "hwaccels"
    }
    for name in ("qsv", "nvenc", "d3d11va"):
        if name in available:
            return name
    return "auto" if available else None


def hwaccel_args(
    video_reencode: bool = False, *, ffmpeg: str | None = None,
    requested: str | None = None, run=None,
) -> list[str]:
    if not video_reencode:
        return []
    choice = requested or probe_hwaccel(ffmpeg, run)
    return ["-hwaccel", choice] if choice else []


def video_encode_argv(
    src: Path | str, dst: Path | str, *, crf: int = 23, vfr: bool = True,
    video_reencode: bool = False, hwaccel: str | None = None,
    ffmpeg: str | None = None, run=None,
) -> list[str]:
    """Video copy by default; re-encode only on explicit opt-in."""
    args = hwaccel_args(video_reencode, ffmpeg=ffmpeg, requested=hwaccel, run=run)
    args += ["-y", "-i", str(src)]
    if video_reencode:
        args += ["-c:v", "libx264", *crf_args(crf)]
        if vfr:
            args += vfr_args()
    else:
        args += ["-c:v", "copy"]
    return [*args, str(dst)]


def merge_wavs_to_temp(wavs: list[Path], out: Path, block: int = 1 << 16) -> Path:
    """Merge mono WAVs ke satu WAV stereo per blok — RAM tidak sebanding durasi."""
    import numpy as np
    import soundfile as sf

    out.parent.mkdir(parents=True, exist_ok=True)
    readers = []
    try:
        for wav in wavs:
            readers.append(sf.SoundFile(str(wav), mode="r"))
        samplerate = readers[0].samplerate
        with sf.SoundFile(
            str(out), mode="w", samplerate=samplerate,
            channels=len(readers), subtype="PCM_16",
        ) as target:
            while True:
                blocks = [reader.read(block, dtype="float32") for reader in readers]
                if not any(len(b) for b in blocks):
                    break
                n = max(len(b) for b in blocks)
                merged = np.zeros((n, len(blocks)), dtype=np.float32)
                for i, b in enumerate(blocks):
                    if b.ndim > 1:
                        b = b.mean(axis=1)
                    merged[: len(b), i] = b
                target.write(merged)
    finally:
        for reader in readers:
            reader.close()
    return out


def verify_audio(path: Path | str, *, ffmpeg: str | None = None) -> bool:
    """Decode penuh sebelum WAV sumber dihapus."""
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-v", "error", "-i", str(path), "-f", "null", "-"],
            capture_output=True, timeout=300,
        )
    except Exception:
        return False
    return result.returncode == 0 and Path(path).exists()


def encode_recording_aac(wavs: list[Path], out_stem: str, cfg: dict) -> Path:
    """WAV → m4a/AAC langsung (tanpa perantara FLAC), verify lalu hapus WAV."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise MediaError("ffmpeg tidak ditemukan — install dengan: winget install ffmpeg")
    container = container_for(cfg)
    out = paths.recordings_dir() / f"{out_stem}.{container}"
    out.parent.mkdir(parents=True, exist_ok=True)
    channels = 2 if len(wavs) > 1 else 1
    merged: Path | None = None
    src = wavs[0]
    if len(wavs) > 1:
        merged = paths.tmp_dir() / f"{out_stem}_merged.wav"
        src = merge_wavs_to_temp(wavs, merged)
    try:
        encode_aac(src, out, channels, ffmpeg=ffmpeg)
        if not verify_audio(out, ffmpeg=ffmpeg):
            raise MediaError(f"output AAC gagal verify: {out.name}")
    except Exception:
        out.unlink(missing_ok=True)
        raise
    finally:
        if merged is not None:
            merged.unlink(missing_ok=True)
    for wav in wavs:
        wav.unlink(missing_ok=True)
    return out


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
