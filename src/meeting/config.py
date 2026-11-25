"""config.json (preferensi) + .env (API key). Key tidak pernah masuk cfg/log."""
from __future__ import annotations

import json
import os
import tempfile

from . import paths

CODEC_CHOICES = ("flac", "opus")
QUALITY_CHOICES = ("archive", "best", "balanced", "compact")

DEFAULTS: dict = {
    "model": "gemini-flash-latest",
    "default_source": "mic",
    "mic_device": None,
    "codec": "flac",          # flac = lossless (default); opus = butuh ffmpeg
    "quality": "archive",     # archive|best|balanced|compact
    "sample_rate": 16000,
    "channels": "auto",       # auto = 1 kanal utk mic/sys, 2 utk --both
}


class ConfigError(RuntimeError):
    pass


def load() -> dict:
    """Muat config.json; buat file default bila belum ada."""
    path = paths.config_file()
    if not path.exists():
        save(DEFAULTS)
        return dict(DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(
            f"config.json korup: {e}. Perbaiki manual di {path} atau hapus untuk reset."
        ) from e
    if not isinstance(data, dict):
        raise ConfigError(
            f"config.json harus objek berisi preferensi, bukan {type(data).__name__}. "
            f"Perbaiki manual di {path} atau hapus untuk reset."
        )
    cfg = dict(DEFAULTS)
    cfg.update(data)
    return cfg


def save(cfg: dict) -> None:
    """Tulis atomik (tmp + replace) agar crash tidak meninggalkan file korup."""
    path = paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def update(**kv) -> dict:
    cfg = load()
    cfg.update(kv)
    save(cfg)
    return cfg


def api_key() -> str | None:
    """Baca GEMINI_API_KEY dari .env. Format KEY=VALUE per baris; abaikan yang lain.
    Kutip yang mengelilingi nilai ("abc"/'abc') dibuang — konvensi .env umum;
    tanpa ini key terkutip lolos ke Gemini dan errornya membingungkan."""
    path = paths.env_file()
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("GEMINI_API_KEY="):
            val = line.split("=", 1)[1].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            return val or None
    return None
