"""config.json (preferensi) + .env (API key). Key tidak pernah masuk cfg/log."""
from __future__ import annotations

import json
import os
import tempfile

from . import paths

DEFAULTS: dict = {"model": "gemini-flash", "default_source": "mic", "mic_device": None}


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
    """Baca GEMINI_API_KEY dari .env. Format KEY=VALUE per baris; abaikan yang lain."""
    path = paths.env_file()
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip() or None
    return None
