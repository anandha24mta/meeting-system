"""Upload → poll → generateContent → tulis output. Satu-satunya modul yang bicara ke Gemini."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
from pathlib import Path

from . import config, indexmd, paths

_AUDIO_MIME = {
    ".mp4": "audio/mp4", ".m4a": "audio/mp4", ".webm": "audio/webm",
    ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".flac": "audio/flac",
    ".wav": "audio/wav", ".opus": "audio/ogg",
}

TRANSCRIPT_PROMPT = (
    "Transkripsikan audio meeting berikut secara lengkap dan verbatim dalam bahasa "
    "Indonesia. Sertakan penanda pembicara jika teridentifikasi (Pembicara 1, "
    "Pembicara 2, dst). Jangan meringkas, jangan menambahkan interpretasi. Jika ada "
    "bagian yang tidak jelas, tulis [tidak jelas]. Format: timestamp perkiraan per "
    "segmen bila memungkinkan."
)

POLL_INTERVAL_SEC = 2.0
POLL_TIMEOUT_SEC = 600.0

# Verbatim generate_content untuk audio sangat panjang tidak reliable dalam satu
# panggilan — model kadang finish_reason=STOP jauh sebelum audio benar-benar habis
# (dikonfirmasi manual: file 74 menit yang sama, sekali berhenti di menit 8, sekali
# tuntas). Di atas threshold ini, audio dipecah jadi beberapa bagian dan
# ditranskripsi terpisah lalu digabung.
CHUNK_THRESHOLD_SEC = 20 * 60
CHUNK_TARGET_SEC = 15 * 60

_SOUNDFILE_EXTS = {".flac", ".wav"}

# Audio nyaris hening total tetap menghasilkan transkrip meyakinkan dari Gemini
# alih-alih "[tidak jelas]" — dikonfirmasi manual: RMS ~0.00003 (~-90 dBFS)
# menghasilkan kalimat pembuka rapat yang sepenuhnya fiktif. Threshold ini jauh
# di bawah ucapan nyata (biasanya > -40 dBFS) jadi tak berisiko salah tolak audio
# pelan yang masih ada isinya.
SILENCE_RMS_THRESHOLD = 0.001


class TranscribeError(RuntimeError):
    pass


def _make_client():
    from google import genai  # import lazy: test tak butuh SDK terpasang utk mock path

    key = config.api_key()
    if not key:
        raise TranscribeError(
            "API key Gemini tidak ditemukan. Isi GEMINI_API_KEY di .env "
            "(salin dari .env.example)."
        )
    return genai.Client(api_key=key)


def _wait_active(client, file, timeout: float = POLL_TIMEOUT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = client.files.get(name=file.name)
        state = getattr(info, "state", None)
        state = str(getattr(state, "name", state))  # enum atau str
        if state == "ACTIVE":
            return
        if state == "FAILED":
            raise TranscribeError(f"Gemini gagal memproses file {file.name}")
        time.sleep(POLL_INTERVAL_SEC)
    raise TranscribeError(f"timeout menunggu file {file.name} menjadi ACTIVE")


def _audio_mime_override(audio_path: Path) -> str | None:
    """Kontainer .mp4/.webm bisa berisi audio-only (voice recorder, WhatsApp).
    Tebakan mime_type default dari ekstensi jadi video/* dan Gemini gagal
    memproses (error code 13 INTERNAL) — dikonfirmasi lewat reproduksi manual.
    Override ke audio/* bila ffprobe (opsional) memastikan tak ada stream video."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    ext = audio_path.suffix.lower()
    if ext not in _AUDIO_MIME:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v", "-show_entries",
             "stream=codec_type", "-of", "csv=p=0", str(audio_path)],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return None
    has_video = bool(out.stdout.strip())
    return None if has_video else _AUDIO_MIME[ext]


def _response_text(resp) -> str:
    try:
        return resp.text or ""
    except Exception:
        return ""


def _probe_duration(audio_path: Path) -> float | None:
    """Durasi dalam detik. soundfile dulu (murah, tanpa subprocess) utk FLAC/WAV
    yang kita sendiri hasilkan; ffprobe (opsional) sbg fallback utk format lain."""
    if audio_path.suffix.lower() in _SOUNDFILE_EXTS:
        try:
            import soundfile as sf
            info = sf.info(str(audio_path))
            return info.frames / info.samplerate
        except Exception:
            pass
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(audio_path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def _chunk_offsets(duration: float) -> list[tuple[float, float]]:
    """[(mulai_detik, panjang_detik), ...] — n potongan sama panjang (≤ CHUNK_TARGET_SEC)
    menutupi seluruh `duration`."""
    n = math.ceil(duration / CHUNK_TARGET_SEC)
    size = duration / n
    return [(i * size, size) for i in range(n)]


def _split_soundfile(audio_path: Path, offsets: list[tuple[float, float]], job_id: str) -> list[Path]:
    import soundfile as sf

    sr = sf.info(str(audio_path)).samplerate
    out_paths: list[Path] = []
    for i, (start, length) in enumerate(offsets):
        data, _ = sf.read(str(audio_path), start=int(start * sr), frames=int(round(length * sr)))
        out = paths.tmp_dir() / f"{job_id}_chunk{i}.flac"
        sf.write(str(out), data, sr, format="FLAC")
        out_paths.append(out)
    return out_paths


def _split_ffmpeg(audio_path: Path, offsets: list[tuple[float, float]], job_id: str) -> list[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TranscribeError(
            "Meeting ini perlu dipecah jadi beberapa bagian (durasi panjang) dan format "
            "filenya butuh ffmpeg untuk itu — install dengan: winget install ffmpeg"
        )
    out_paths: list[Path] = []
    for i, (start, length) in enumerate(offsets):
        out = paths.tmp_dir() / f"{job_id}_chunk{i}.flac"
        rc = subprocess.run(
            [ffmpeg, "-y", "-ss", str(start), "-i", str(audio_path), "-t", str(length),
             "-ac", "1", "-ar", "16000", str(out)],
            capture_output=True,
        )
        if rc.returncode != 0 or not out.exists():
            for p in out_paths:
                p.unlink(missing_ok=True)
            raise TranscribeError(f"ffmpeg gagal memecah bagian {i + 1}: {audio_path.name}")
        out_paths.append(out)
    return out_paths


def _prepare_chunks(audio_path: Path, job_id: str) -> list[tuple[Path, float]]:
    """[(path_potongan, mulai_detik), ...]. Satu elemen [(audio_path, 0.0)] bila
    durasi tak diketahui atau di bawah CHUNK_THRESHOLD_SEC — jalur asli, tak berubah."""
    duration = _probe_duration(audio_path)
    if duration is None or duration <= CHUNK_THRESHOLD_SEC:
        return [(audio_path, 0.0)]
    offsets = _chunk_offsets(duration)
    paths.tmp_dir().mkdir(parents=True, exist_ok=True)
    if audio_path.suffix.lower() in _SOUNDFILE_EXTS:
        chunk_paths = _split_soundfile(audio_path, offsets, job_id)
    else:
        chunk_paths = _split_ffmpeg(audio_path, offsets, job_id)
    return [(cp, start) for cp, (start, _length) in zip(chunk_paths, offsets)]


def _is_silent(audio_path: Path) -> bool:
    """True bila audio (yg soundfile bisa baca) nyaris hening total sepanjang
    durasinya. File yg soundfile tak bisa baca (mp4/mp3 mentah dll) tidak dicek —
    lolos ke Gemini seperti biasa (lihat SILENCE_RMS_THRESHOLD)."""
    if audio_path.suffix.lower() not in _SOUNDFILE_EXTS:
        return False
    try:
        import numpy as np
        import soundfile as sf
        data, _sr = sf.read(str(audio_path), dtype="float32", always_2d=False)
        if data.size == 0:
            return True
        return float(np.sqrt(np.mean(np.square(data)))) < SILENCE_RMS_THRESHOLD
    except Exception:
        return False


def _transcribe_chunk(client, model: str, chunk_path: Path) -> str:
    if _is_silent(chunk_path):
        raise TranscribeError(
            "audio nyaris hening total — tidak ada suara terdeteksi, transkripsi "
            "dibatalkan (kemungkinan mic/sumber tidak menangkap apa pun)"
        )
    mime_type = _audio_mime_override(chunk_path)
    upload_kwargs = {"file": str(chunk_path)}
    if mime_type:
        upload_kwargs["config"] = {"mime_type": mime_type}
    uploaded = client.files.upload(**upload_kwargs)
    try:
        _wait_active(client, uploaded)
        try:
            resp = client.models.generate_content(
                model=model, contents=[TRANSCRIPT_PROMPT, uploaded]
            )
        except TranscribeError:
            raise
        except Exception as e:
            raise TranscribeError(f"Gemini generate_content gagal: {e}") from e
        text = _response_text(resp).strip()
        if not text:
            raise TranscribeError(
                "respons Gemini tanpa teks — job ditandai gagal, tidak ada file kosong ditulis"
            )
        return text
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass  # best-effort; file Gemini expire otomatis 48 jam


def transcribe_audio(audio_path: Path, job: dict, client=None) -> dict:
    if client is None:
        client = _make_client()

    from . import indexmd as _idx  # durasi utk header
    dur = job.get("duration_sec")
    durasi = _idx.fmt_duration(dur) if dur else "-"

    chunks = _prepare_chunks(audio_path, job["id"])
    is_split = len(chunks) > 1
    parts: list[str] = []
    try:
        for i, (chunk_path, offset) in enumerate(chunks):
            chunk_text = _transcribe_chunk(client, job["model"], chunk_path)
            if is_split:
                parts.append(f"--- Bagian {i + 1} (mulai ≈ {_idx.fmt_duration(offset)}) ---\n\n{chunk_text}")
            else:
                parts.append(chunk_text)
    finally:
        if is_split:
            for chunk_path, _offset in chunks:
                chunk_path.unlink(missing_ok=True)
    text = "\n\n".join(parts)

    outdir = paths.output_dir() / job["id"]
    outdir.mkdir(parents=True, exist_ok=True)

    meta = {
        "id": job["id"],
        "created_at": job["created_at"],
        "duration_sec": job.get("duration_sec"),
        "device": job.get("device"),
        "source": job.get("source"),
        "model": job["model"],
        "status": "done",
        "transcribed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "audio": job.get("audio"),
    }
    header = (
        f"# Transkrip — {job['id']}\n\n"
        f"- Tanggal: {job['created_at']}\n"
        f"- Durasi: {durasi}\n"
        f"- Device: {job.get('device') or '-'}\n"
        f"- Sumber: {job.get('source')}\n"
        f"- Model: {job['model']}\n\n---\n\n"
    )
    (outdir / "transcript.md").write_text(header + text + "\n", encoding="utf-8")
    (outdir / "transcript.txt").write_text(text + "\n", encoding="utf-8")
    (outdir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return meta
