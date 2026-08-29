"""Upload → poll → generateContent → tulis output. Satu-satunya modul yang bicara ke Gemini."""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import config, indexmd, paths

TRANSCRIPT_PROMPT = (
    "Transkripsikan audio meeting berikut secara lengkap dan verbatim dalam bahasa "
    "Indonesia. Sertakan penanda pembicara jika teridentifikasi (Pembicara 1, "
    "Pembicara 2, dst). Jangan meringkas, jangan menambahkan interpretasi. Jika ada "
    "bagian yang tidak jelas, tulis [tidak jelas]. Format: timestamp perkiraan per "
    "segmen bila memungkinkan."
)

POLL_INTERVAL_SEC = 2.0
POLL_TIMEOUT_SEC = 600.0


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


def _response_text(resp) -> str:
    try:
        return resp.text or ""
    except Exception:
        return ""


def transcribe_audio(audio_path: Path, job: dict, client=None) -> dict:
    if client is None:
        client = _make_client()

    from . import indexmd as _idx  # durasi utk header
    dur = job.get("duration_sec")
    durasi = _idx.fmt_duration(dur) if dur else "-"

    uploaded = client.files.upload(file=str(audio_path))
    try:
        _wait_active(client, uploaded)
        try:
            resp = client.models.generate_content(
                model=job["model"], contents=[TRANSCRIPT_PROMPT, uploaded]
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
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass  # best-effort; file Gemini expire otomatis 48 jam

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
