# Meeting Transcriber Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CLI Windows lokal untuk merekam meeting (mic / audio sistem / keduanya) dan mentranskripsinya via Gemini API di worker background, semua hasil di disk lokal.

**Architecture:** CLI + worker terpisah. Recorder menulis WAV mono 16 kHz incremental (crash-safe) ke `data/tmp/`; saat stop, job JSON ditulis ke `data/queue/pending/` lalu worker detached (file-lock, FIFO) memfinalisasi WAV→FLAC, meng-upload ke Gemini File API, dan menulis transkrip + INDEX.md. Terminal bebas < 10 detik setelah stop karena finalisasi FLAC terjadi di worker, bukan di terminal. Tombol floating tkinter adalah frontend kedua di atas core yang sama (session-file lock + stop-flag file lintas proses).

**Tech Stack:** Python >= 3.10, `sounddevice` (mic), `soundcard` (WASAPI loopback), `soundfile` (WAV/FLAC), `numpy`, `google-genai` (Gemini Developer API), stdlib `argparse` + `tkinter`. Test: `pytest`.

## Global Constraints

- Runtime target: **Windows native** (PowerShell/Terminal). Pengembangan & unit test dari **WSL2** — semua backend audio (`sounddevice`, `soundcard`) di-import **lazy di dalam fungsi** sehingga unit test jalan tanpa hardware; hardware + API key diverifikasi lewat integration test opt-in (Task 14) dan checklist manual.
- `requires-python = ">=3.10"`; dependensi runtime hanya: `numpy`, `soundfile`, `sounddevice`, `soundcard`, `google-genai` (+ dev extra `pytest`). Tidak ada framework lain.
- Semua data di bawah root proyek, dapat dioverride env `MEETING_HOME` (dipakai unit test): `data/recordings/`, `data/queue/pending/`, `data/queue/done/`, `data/queue/failed/`, `data/output/`, `data/tmp/`, `data/worker.log`, `data/record_session.json`; `INDEX.md`, `config.json`, `.env` di root proyek.
- **Audio final tidak pernah dihapus otomatis oleh tool**, apa pun yang terjadi pada transkripsi (prinsip spec). WAV sementara di `data/tmp/` dihapus hanya setelah FLAC final tersimpan terverifikasi.
- Model Gemini hanya dari `config.json` (`"model": "gemini-flash"` alias boleh; nama persis bisa diganti tanpa ubah kode).
- Prompt transkripsi persis seperti di spec (Bahasa Indonesia, verbatim, [tidak jelas], timestamp perkiraan).
- Transkrip mentah saja — TIDAK ada ringkasan/action items/summary (non-tujuan spec).
- Bahasa output user-facing CLI: Indonesia, sesuai spec.
- Commit kecil dan sering; setiap task diakhiri commit.

---

## File Structure

```
meeting-system/
├── pyproject.toml            # paket `meeting`, entry point `meeting` + `meeting-worker`
├── .gitignore                # .env, data/, __pycache__
├── .env.example              # GEMINI_API_KEY=...
├── config.json               # dibuat saat first-run; model, default_source, mic_device
├── INDEX.md                  # tabel seluruh meeting (dikelola index.py)
├── README.md
├── src/
│   └── meeting/
│       ├── __init__.py
│       ├── paths.py          # semua path (MEETING_HOME override) + ensure_dirs
│       ├── config.py         # load/save config.json + .env (GEMINI_API_KEY)
│       ├── queuer.py         # tulis job JSON ke pending/, spawn worker detached
│       ├── finalizer.py      # WAV → FLAC mono 16 kHz (mic) / stereo (both)
│       ├── indexmd.py        # upsert baris INDEX.md per status transition
│       ├── worker.py         # lock, loop FIFO, orkestrasi finalisasi+transkripsi
│       ├── transcribe.py     # Gemini upload/poll/generate/delete + tulis output
│       ├── recorder.py       # mic (sounddevice) + loopback (soundcard), WAV incremental
│       ├── session.py        # session-file lock + stop-flag (anti-dobel lintas proses)
│       ├── recover.py        # deteksi sesi yatim → tawarkan finalisasi/buang
│       ├── cli.py            # argparse: record/transcribe/status/list/retry/devices/button
│       ├── devices.py        # listing device mic + speaker loopback
│       └── button.py         # floating tkinter record button
└── tests/
    ├── conftest.py           # fixture tmp MEETING_HOME + fake config
    ├── test_paths_config.py
    ├── test_queuer.py
    ├── test_finalizer.py
    ├── test_indexmd.py
    ├── test_worker.py
    ├── test_transcribe.py
    ├── test_session.py
    └── test_cli.py
```

Modul audio (`recorder.py`, `devices.py`) meng-import `sounddevice`/`soundcard` **di dalam fungsi** agar modul bisa di-import di WSL tanpa library sistem; unit test mem-mock via monkeypatch pada titik import itu.

---

## Task 1: Scaffold — pyproject, .gitignore, paths module

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `src/meeting/__init__.py`, `src/meeting/paths.py`
- Test: `tests/conftest.py`, `tests/test_paths_config.py`

**Interfaces:**
- Produces: `paths.home() -> Path` (root data; env `MEETING_HOME` override, default = root proyek), `paths.recordings_dir()`, `paths.queue_dir(sub: str)`, `paths.output_dir()`, `paths.tmp_dir()`, `paths.worker_log()`, `paths.session_file()`, `paths.index_file()`, `paths.config_file()`, `paths.env_file()`, `paths.ensure_dirs() -> None`, dan `paths.project_root() -> Path`.

- [ ] **Step 1: Write the failing test**

`tests/conftest.py`:

```python
import json
from pathlib import Path

import pytest


@pytest.fixture
def meeting_home(tmp_path, monkeypatch):
    """Isolasi seluruh data tool ke tmp dir lewat MEETING_HOME."""
    home = tmp_path / "meeting-home"
    home.mkdir()
    monkeypatch.setenv("MEETING_HOME", str(home))
    return home


@pytest.fixture
def config(meeting_home):
    """config.json valid + API key dummy."""
    from meeting import paths

    paths.ensure_dirs()
    paths.config_file().write_text(
        json.dumps({"model": "gemini-flash", "default_source": "mic", "mic_device": None}),
        encoding="utf-8",
    )
    paths.env_file().write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")
    return paths.config_file()
```

`tests/test_paths_config.py` (bagian paths):

```python
from meeting import paths


def test_paths_honor_meeting_home(meeting_home):
    assert paths.home() == meeting_home
    assert paths.queue_dir("pending") == meeting_home / "data" / "queue" / "pending"
    assert paths.tmp_dir() == meeting_home / "data" / "tmp"
    assert paths.index_file() == meeting_home / "INDEX.md"


def test_ensure_dirs_creates_all(meeting_home):
    paths.ensure_dirs()
    for d in (
        paths.recordings_dir(),
        paths.queue_dir("pending"),
        paths.queue_dir("done"),
        paths.queue_dir("failed"),
        paths.output_dir(),
        paths.tmp_dir(),
    ):
        assert d.is_dir(), f"missing {d}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_paths_config.py -v` (dari root proyek)
Expected: FAIL — `ModuleNotFoundError: No module named 'meeting'`

- [ ] **Step 3: Write minimal implementation**

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "meeting-transcriber"
version = "0.1.0"
description = "Rekam meeting Windows, transkripsi otomatis via Gemini"
requires-python = ">=3.10"
dependencies = [
    "numpy",
    "soundfile",
    "sounddevice",
    "soundcard",
    "google-genai",
]

[project.optional-dependencies]
dev = ["pytest"]

[project.scripts]
meeting = "meeting.cli:main"
meeting-worker = "meeting.worker:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.gitignore`:

```
.env
data/
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
build/
dist/
.superpowers/
.omc/
```

`.env.example`:

```
GEMINI_API_KEY=paste-your-key-here
```

`src/meeting/__init__.py`:

```python
"""Meeting Transcriber — rekam & transkripsi meeting lokal."""
```

`src/meeting/paths.py`:

```python
"""Semua path tool. Tunggal sumber kebenaran; MEETING_HOME meng-override root."""
from __future__ import annotations

import os
from pathlib import Path

_QUEUE_SUBS = ("pending", "done", "failed")


def project_root() -> Path:
    """Root repo/paket — dipakai untuk default home dan resolve resource."""
    return Path(__file__).resolve().parent.parent.parent


def home() -> Path:
    """Root data tool. Env MEETING_HOME override (testing / lokasi lain)."""
    env = os.environ.get("MEETING_HOME")
    return Path(env).resolve() if env else project_root()


def recordings_dir() -> Path:
    return home() / "data" / "recordings"


def queue_dir(sub: str) -> Path:
    if sub not in _QUEUE_SUBS:
        raise ValueError(f"sub must be one of {_QUEUE_SUBS}, got {sub!r}")
    return home() / "data" / "queue" / sub


def output_dir() -> Path:
    return home() / "data" / "output"


def tmp_dir() -> Path:
    return home() / "data" / "tmp"


def worker_log() -> Path:
    return home() / "data" / "worker.log"


def session_file() -> Path:
    return home() / "data" / "record_session.json"


def stop_flag() -> Path:
    return tmp_dir() / "record.stop"


def index_file() -> Path:
    return home() / "INDEX.md"


def config_file() -> Path:
    return home() / "config.json"


def env_file() -> Path:
    return home() / ".env"


def ensure_dirs() -> None:
    for d in (
        recordings_dir(),
        *(queue_dir(s) for s in _QUEUE_SUBS),
        output_dir(),
        tmp_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Install editable + run test to verify it passes**

Run: `pip install -e . && python -m pytest tests/test_paths_config.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore .env.example src/ tests/
git commit -m "feat: scaffold package, paths module with MEETING_HOME override"
```

---

## Task 2: Config module — config.json + .env loading

**Files:**
- Create: `src/meeting/config.py`
- Test: `tests/test_paths_config.py` (extend)

**Interfaces:**
- Consumes: `paths.config_file()`, `paths.env_file()`
- Produces: `config.load() -> dict` (merge default + file; raise `ConfigError` bila file korup), `config.save(cfg: dict) -> None` (atomic write), `config.update(**kv) -> dict`, `DEFAULTS: dict = {"model": "gemini-flash", "default_source": "mic", "mic_device": None}`, `config.api_key() -> str | None` (baca `.env`, hanya `GEMINI_API_KEY`; return None bila file/kosong), exception `ConfigError(RuntimeError)`. Fungsi `load()` membuat `config.json` dengan default bila belum ada (first-run).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_paths_config.py`:

```python
import json

import pytest

from meeting import config


def test_load_creates_default_on_first_run(meeting_home):
    cfg = config.load()
    assert cfg == {"model": "gemini-flash", "default_source": "mic", "mic_device": None}
    assert config_file_json_exists()


def config_file_json_exists():
    from meeting import paths
    return paths.config_file().is_file()


def test_load_merges_file_over_defaults(meeting_home):
    from meeting import paths
    paths.config_file().write_text('{"model": "gemini-2.5-pro"}', encoding="utf-8")
    cfg = config.load()
    assert cfg["model"] == "gemini-2.5-pro"
    assert cfg["default_source"] == "mic"  # default tetap terisi


def test_load_rejects_corrupt_json(meeting_home):
    from meeting import paths
    paths.config_file().write_text("{not json", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load()


def test_update_persists_atomically(meeting_home):
    cfg = config.update(default_source="sys")
    assert cfg["default_source"] == "sys"
    assert config.load()["default_source"] == "sys"


def test_api_key_from_env_file(meeting_home):
    from meeting import paths
    assert config.api_key() is None  # belum ada .env
    paths.env_file().write_text("GEMINI_API_KEY=abc123\n", encoding="utf-8")
    assert config.api_key() == "abc123"


def test_api_key_never_leaks_into_cfg(meeting_home):
    from meeting import paths
    paths.env_file().write_text("GEMINI_API_KEY=abc123\n", encoding="utf-8")
    assert "api_key" not in config.load()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_paths_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'config'`

- [ ] **Step 3: Write minimal implementation**

`src/meeting/config.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_paths_config.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add src/meeting/config.py tests/test_paths_config.py
git commit -m "feat: config module with atomic save and .env API key loading"
```

---

## Task 3: Job schema + queuer — tulis job JSON, spawn worker detached

**Files:**
- Create: `src/meeting/queuer.py`
- Test: `tests/test_queuer.py`

**Interfaces:**
- Consumes: `paths.queue_dir("pending")`, `paths.ensure_dirs()`
- Produces:
  - Job schema v1 (contract lintas modul — worker & transcribe mengandalkan field ini):

```python
{
  "id": "2026-08-29_1400-standup",     # = nama folder output & baris INDEX
  "created_at": "2026-08-29T14:00:05",  # ISO local time
  "source": "mic" | "sys" | "both" | "file",
  "audio": "recordings/2026-08-29_1400-standup.flac",   # relatif thd home()
  "wav": "tmp/2026-08-29_1400-standup_mic.wav",         # relatif; hanya utk record; dihapus worker setelah FLAC diverify
  "original_path": null,                # path absolut file asli utk source=="file"
  "needs_finalization": true,           # true = worker harus WAV→FLAC dulu
  "duration_sec": 3600.5,               # durasi rekaman (bukan transkripsi)
  "device": "Headset (Jabra)",          # label device input
  "model": "gemini-flash",              # snapshot saat enqueue (config bisa berubah)
  "status": "pending"
}
```

  - `queuer.enqueue(job: dict) -> Path` (tulis JSON atomik `pending/<id>.json`; raise `ValueError` bila `id` kosong/illegal), `queuer.spawn_worker() -> None` (`subprocess.Popen` entry point `meeting-worker`, detached: `CREATE_NO_WINDOW`/`DETACHED_PROCESS` di Windows, `start_new_session=True` di POSIX; tidak raise bila gagal spawn — worker bisa juga dijalankan manual), `queuer.is_valid_id(s: str) -> bool` (regex `^[A-Za-z0-9_\-]+$`).

- [ ] **Step 1: Write the failing test**

`tests/test_queuer.py`:

```python
import json

import pytest

from meeting import queuer


def make_job(**over):
    job = {
        "id": "2026-08-29_1400-standup",
        "created_at": "2026-08-29T14:00:05",
        "source": "mic",
        "audio": "recordings/2026-08-29_1400-standup.flac",
        "wav": "tmp/2026-08-29_1400-standup_mic.wav",
        "original_path": None,
        "needs_finalization": True,
        "duration_sec": 3600.5,
        "device": "Headset (Jabra)",
        "model": "gemini-flash",
        "status": "pending",
    }
    job.update(over)
    return job


def test_enqueue_writes_job_json(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    p = queuer.enqueue(make_job())
    assert p == paths.queue_dir("pending") / "2026-08-29_1400-standup.json"
    assert json.loads(p.read_text(encoding="utf-8"))["id"] == "2026-08-29_1400-standup"


def test_enqueue_atomic_no_tmp_left(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())
    leftovers = [f for f in paths.queue_dir("pending").iterdir() if f.suffix == ".tmp"]
    assert leftovers == []


def test_enqueue_rejects_bad_id(meeting_home):
    paths_ = queuer.paths
    paths_.ensure_dirs()
    with pytest.raises(ValueError):
        queuer.enqueue(make_job(id="../evil"))
    with pytest.raises(ValueError):
        queuer.enqueue(make_job(id="sp ace"))


def test_spawn_worker_popen_called(meeting_home, monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, *a, **kw):
            calls.append((a, kw))

    monkeypatch.setattr(queuer.subprocess, "Popen", FakePopen)
    queuer.spawn_worker()
    assert len(calls) == 1
    args, kw = calls[0]
    cmd = args[0]
    assert "meeting.worker" in cmd  # python -m meeting.worker
    assert kw["stdout"] == queuer.subprocess.DEVNULL  # detached: tak menempel di terminal
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_queuer.py -v`
Expected: FAIL — `ImportError: cannot import name 'queuer'`

- [ ] **Step 3: Write minimal implementation**

`src/meeting/queuer.py`:

```python
"""Tulis job ke pending/ + spawn worker detached. FIFO = urutan nama file (created_at prefix)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import paths

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def is_valid_id(s: str) -> bool:
    return bool(s) and bool(_ID_RE.match(s))


def enqueue(job: dict) -> Path:
    """Tulis job JSON atomik ke pending/. ID menjadi nama file."""
    if not is_valid_id(job.get("id", "")):
        raise ValueError(f"id job ilegal: {job.get('id')!r} (hanya [A-Za-z0-9_-])")
    paths.ensure_dirs()
    dest = paths.queue_dir("pending") / f"{job['id']}.json"
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(job, f, indent=2, ensure_ascii=False)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dest


def spawn_worker() -> None:
    """Jalankan meeting-worker detached. Best-effort: worker juga bisa dipanggil manual."""
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(
            [sys.executable, "-m", "meeting.worker"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            **kwargs,
        )
    except OSError:
        # Worker tidak ter-spawn bukan error fatal job — user dapat jalankan manual.
        pass


def new_job_id(name: str | None = None) -> str:
    """ID default: tanggal-jam; --name di-append bila diberikan."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    return f"{stamp}-{name}" if name else stamp
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_queuer.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/meeting/queuer.py tests/test_queuer.py
git commit -m "feat: job queue writer with atomic enqueue and detached worker spawn"
```

---

## Task 4: Finalizer — WAV → FLAC mono/stereo 16 kHz

**Files:**
- Create: `src/meeting/finalizer.py`
- Test: `tests/test_finalizer.py`

**Interfaces:**
- Consumes: `paths.recordings_dir()`, `paths.tmp_dir()`
- Produces: `finalizer.finalize(wav_paths: list[Path], out_stem: str) -> Path`
  - 1 WAV → FLAC **mono** 16 kHz; 2 WAV → FLAC **stereo** 16 kHz (mic = kiri, sistem = kanan), kanal pendek dipad silence agar panjang sama, lalu WAV dihapus **hanya setelah** FLAC diverify terbaca (`soundfile.info` OK). Return path FLAC final di `recordings/`. Path output via `paths.recordings_dir()` — test sudah terisolasi lewat fixture `MEETING_HOME`.
  - Raise `FinalizeError(RuntimeError)` bila input kosong/tidak ada; WAV yatim tetap utuh saat error (tidak ada penghapusan di jalur error).
  - `finalizer.resolve_audio(job: dict) -> Path` — path audio untuk worker: `job["wav"]` bila `needs_finalization` else `job["audio"]`; relatif diresolve thd `home()/data/`, absolut dipakai apa adanya (untuk `source == "file"`).

- [ ] **Step 1: Write the failing test**

`tests/test_finalizer.py` (membuat WAV sintetis via `soundfile` — jalan di WSL tanpa hardware):

```python
import numpy as np
import pytest
import soundfile as sf

from meeting import finalizer


@pytest.fixture
def wav_mono(meeting_home, tmp_path):
    """WAV mono 16 kHz, 1 detik sine 440 Hz, amplitude 0.3."""
    sr = 16000
    t = np.linspace(0, 1.0, sr, endpoint=False)
    data = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    p = tmp_path / "sess_mic.wav"
    sf.write(p, data, sr, subtype="PCM_16")
    return p


def test_finalize_mono_produces_flac(meeting_home, wav_mono):
    from meeting import paths
    paths.ensure_dirs()
    out = finalizer.finalize([wav_mono], "2026-08-29_1400")
    assert out == paths.recordings_dir() / "2026-08-29_1400.flac"
    assert out.exists()
    info = sf.info(out)
    assert info.samplerate == 16000
    assert info.channels == 1
    assert info.format == "FLAC"


def test_finalize_deletes_wav_after_verify(meeting_home, wav_mono):
    from meeting import paths
    paths.ensure_dirs()
    finalizer.finalize([wav_mono], "2026-08-29_1400")
    assert not wav_mono.exists()


def test_finalize_two_wavs_makes_stereo(meeting_home, tmp_path):
    from meeting import paths
    paths.ensure_dirs()
    sr = 16000
    t = np.linspace(0, 0.5, sr // 2, endpoint=False)
    left = (0.2 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    right = (0.2 * np.sin(2 * np.pi * 500 * t)).astype(np.float32)
    w1, w2 = tmp_path / "a.wav", tmp_path / "b.wav"
    sf.write(w1, left, sr, subtype="PCM_16")
    sf.write(w2, right, sr, subtype="PCM_16")

    out = finalizer.finalize([w1, w2], "2026-08-29_1500")
    data, sr_out = sf.read(out)
    assert sr_out == 16000
    assert data.ndim == 2 and data.shape[1] == 2
    # kanal kiri = mic (w1), kanan kanan = sistem (w2)
    assert np.allclose(data[:, 0], left, atol=1e-3)
    assert np.allclose(data[:, 1], right, atol=1e-3)


def test_finalize_pads_unequal_lengths(meeting_home, tmp_path):
    """Stream sys biasanya lebih panjang/pendek dari mic — pad dengan silence."""
    from meeting import paths
    paths.ensure_dirs()
    sr = 16000
    long_data = np.zeros(sr, dtype=np.float32)          # 1.0 s
    short_data = np.zeros(sr // 2, dtype=np.float32)    # 0.5 s
    w1, w2 = tmp_path / "long.wav", tmp_path / "short.wav"
    sf.write(w1, long_data, sr, subtype="PCM_16")
    sf.write(w2, short_data, sr, subtype="PCM_16")

    out = finalizer.finalize([w1, w2], "2026-08-29_1600")
    data, _ = sf.read(out)
    assert data.shape == (sr, 2)  # ikuti yang terpanjang
    assert np.allclose(data[sr // 2:, 1], 0.0)  # kanal kanan padded silence


def test_finalize_keeps_wav_on_error(meeting_home, wav_mono):
    with pytest.raises(finalizer.FinalizeError):
        finalizer.finalize([wav_mono.with_name("missing.wav")], "x")
    assert wav_mono.exists()  # WAV lain tak tersentuh di jalur error


def test_finalize_rejects_empty_list(meeting_home):
    with pytest.raises(finalizer.FinalizeError):
        finalizer.finalize([], "x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_finalizer.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/meeting/finalizer.py`:

```python
"""WAV → FLAC 16 kHz. Mono untuk mic/sys; stereo (mic=kiri, sys=kanan) untuk --both.
WAV dihapus hanya setelah FLAC diverify terbaca — prinsip: audio aman dulu, bersih-bersih belakangan."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from . import paths


class FinalizeError(RuntimeError):
    pass


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

    channels = [_read_16k_mono(w) for w in wav_paths]
    if len(channels) == 1:
        merged = channels[0]
    else:
        # pad kanal pendek dengan silence agar panjang sama
        n = max(len(c) for c in channels)
        merged = np.zeros((n, len(channels)), dtype=np.float32)
        for i, c in enumerate(channels):
            merged[: len(c), i] = c

    sf.write(out_path, merged, 16000, format="FLAC")

    # verify sebelum hapus WAV — FLAC korup tidak boleh menghapus sumber
    info = sf.info(out_path)
    if info.frames == 0 or info.samplerate != 16000:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_finalizer.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/meeting/finalizer.py tests/test_finalizer.py
git commit -m "feat: WAV to FLAC finalizer, mono and both-mode stereo, verify-before-delete"
```

---

## Task 5: INDEX.md — tabel meeting, upsert per transisi status

**Files:**
- Create: `src/meeting/indexmd.py`
- Test: `tests/test_indexmd.py`

**Interfaces:**
- Consumes: `paths.index_file()`
- Produces: `indexmd.upsert(row: dict) -> None` — kolom: `id`, `tanggal`, `durasi`, `status`, `path`. Buat file dengan header bila belum ada; baris baru disisipkan **tepat di bawah header** (terbaru di atas karena kita selalu upsert baru); baris yang ada (match `id`) di-update in-place. `indexmd.rows() -> list[dict]` — parse tabel untuk `meeting list`. `indexmd.fmt_duration(sec: float) -> str` — `H:MM:SS` / `M:SS`.

- [ ] **Step 1: Write the failing test**

`tests/test_indexmd.py`:

```python
from meeting import indexmd


def row(**over):
    r = {
        "id": "2026-08-29_1400-standup",
        "tanggal": "2026-08-29 14:00",
        "durasi": "1:00:00",
        "status": "pending",
        "path": "data/output/2026-08-29_1400-standup/transcript.md",
    }
    r.update(over)
    return r


def test_creates_file_with_header(meeting_home):
    indexmd.upsert(row())
    from meeting import paths
    text = paths.index_file().read_text(encoding="utf-8")
    assert text.startswith("# INDEX\n\n")
    assert "| id | tanggal | durasi | status | path |" in text
    assert "2026-08-29_1400-standup" in text


def test_upsert_updates_existing_row_in_place(meeting_home):
    from meeting import paths
    indexmd.upsert(row())
    indexmd.upsert(row(status="done"))
    text = paths.index_file().read_text(encoding="utf-8")
    # NB: kolom path juga memuat id — hitung baris data, bukan substring total
    data_lines = [ln for ln in text.splitlines() if ln.startswith("| 2026-08-29_1400-standup |")]
    assert len(data_lines) == 1
    assert "| done |" in text


def test_newest_row_inserted_below_header(meeting_home):
    from meeting import paths
    indexmd.upsert(row())
    indexmd.upsert(row(id="2026-08-29_1600", tanggal="2026-08-29 16:00"))
    lines = paths.index_file().read_text(encoding="utf-8").splitlines()
    data_lines = [ln for ln in lines if ln.startswith("| 2026")]
    assert data_lines[0].startswith("| 2026-08-29_1600")  # terbaru di atas


def test_rows_parses_table(meeting_home):
    indexmd.upsert(row(status="done"))
    rows = indexmd.rows()
    assert len(rows) == 1
    assert rows[0]["id"] == "2026-08-29_1400-standup"
    assert rows[0]["status"] == "done"


def test_fmt_duration():
    assert indexmd.fmt_duration(3661.0) == "1:01:01"
    assert indexmd.fmt_duration(59.0) == "0:59"
    assert indexmd.fmt_duration(0.0) == "0:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_indexmd.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/meeting/indexmd.py`:

```python
"""INDEX.md: tabel seluruh meeting. Satu baris per id; upsert per transisi status."""
from __future__ import annotations

import re

from . import paths

_HEADER = "# INDEX\n\n| id | tanggal | durasi | status | path |\n|---|---|---|---|---|\n"
_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$")


def _row_line(r: dict) -> str:
    return f"| {r['id']} | {r['tanggal']} | {r['durasi']} | {r['status']} | {r['path']} |"


def upsert(row: dict) -> None:
    path = paths.index_file()
    if not path.exists():
        path.write_text(_HEADER + _row_line(row) + "\n", encoding="utf-8")
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    replaced = False
    sep_seen = False
    for ln in lines:
        if ln.startswith("|---"):
            sep_seen = True
            out.append(ln)
            continue
        m = _ROW_RE.match(ln)
        if sep_seen and m and m.group(1) == row["id"]:
            out.append(_row_line(row))  # update in-place
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        # sisip tepat di bawah header (terbaru di atas)
        for i, ln in enumerate(out):
            if ln.startswith("|---"):
                out.insert(i + 1, _row_line(row))
                break
        else:
            out.append(_HEADER)
            out.append(_row_line(row))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def rows() -> list[dict]:
    """Parse tabel jadi list of dict, urut file (terbaru di atas)."""
    path = paths.index_file()
    if not path.exists():
        return []
    result = []
    past_sep = False
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith("|---"):
            past_sep = True
            continue
        m = _ROW_RE.match(ln)
        if past_sep and m:
            result.append(
                {
                    "id": m.group(1),
                    "tanggal": m.group(2),
                    "durasi": m.group(3),
                    "status": m.group(4),
                    "path": m.group(5),
                }
            )
    return result


def fmt_duration(sec: float) -> str:
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_indexmd.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/meeting/indexmd.py tests/test_indexmd.py
git commit -m "feat: INDEX.md upsert manager with newest-first ordering"
```

---

## Task 6: Gemini transcribe — upload, poll, generate, delete, tulis output

**Files:**
- Create: `src/meeting/transcribe.py`
- Test: `tests/test_transcribe.py`

**Interfaces:**
- Consumes: `config.api_key()`, `config.load()["model"]`, `paths.output_dir()`
- Produces:
  - `transcribe.TRANSCRIPT_PROMPT: str` — prompt persis dari spec.
  - `transcribe.transcribe_audio(audio_path: Path, job: dict, client: google.genai.Client | None = None) -> dict` — upload → poll `files.get` hingga `ACTIVE` (timeout 10 menit) → `generate_content(model=job["model"])` → tulis `output/<id>/transcript.md`, `transcript.txt`, `meta.json` → return meta dict. Klien di-inject agar test bisa mock; default buat `Client(api_key=...)` (raise `TranscribeError` bila key kosong). Respons tanpa teks → raise `TranscribeError` (job → failed, tidak menulis file kosong). `files.delete` di `finally` (best-effort, error diabaikan — file Gemini otomatis expire 48 jam).
  - `transcribe.TranscribeError(RuntimeError)`.

- [ ] **Step 1: Write the failing test**

`tests/test_transcribe.py` — semua interaksi Gemini di-mock; tidak ada network:

```python
import json
from pathlib import Path

import pytest

from meeting import transcribe


class FakeFile:
    def __init__(self, name="files/fake-1"):
        self.name = name
        self.state = "PROCESSING"


class FakeFiles:
    """files.upload/get/delete dengan state transisi PROCESSING -> ACTIVE."""

    def __init__(self, text="Halo, ini transkrip.", fail_generate=False):
        self.text = text
        self.fail_generate = fail_generate
        self.uploaded = []
        self.deleted = []
        self.get_calls = 0

    def upload(self, *, file, config=None):
        self.uploaded.append(str(file))
        return FakeFile()

    def get(self, *, name):
        self.get_calls += 1
        f = FakeFile(name)
        f.state = "ACTIVE" if self.get_calls >= 2 else "PROCESSING"
        return f

    def delete(self, *, name):
        self.deleted.append(name)


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, files, fail_generate=False):
        self.files = files
        self.fail_generate = fail_generate

    def generate_content(self, *, model, contents):
        if self.fail_generate:
            r = FakeResponse(None)
            # emulasi SDK: response.text raise bila tidak ada parts
            raise ValueError("no text")
        return FakeResponse(self.files.text)


class FakeClient:
    def __init__(self, text="Halo, ini transkrip.", fail_generate=False):
        self.files = FakeFiles(text)
        self.models = FakeModels(self.files, fail_generate)


def make_job(source="mic", model="gemini-flash"):
    return {
        "id": "2026-08-29_1400-standup",
        "created_at": "2026-08-29T14:00:05",
        "source": source,
        "audio": "recordings/2026-08-29_1400-standup.flac",
        "wav": None,
        "original_path": None,
        "needs_finalization": False,
        "duration_sec": 3661.0,
        "device": "Headset (Jabra)",
        "model": model,
        "status": "pending",
    }


@pytest.fixture
def audio(meeting_home, tmp_path):
    import numpy as np
    import soundfile as sf
    t = np.linspace(0, 0.5, 8000, endpoint=False)
    data = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    p = meeting_home / "data" / "recordings" / "a.flac"
    p.parent.mkdir(parents=True, exist_ok=True)
    sf.write(p, data, 16000, format="FLAC")
    return p


def test_transcribe_writes_outputs(meeting_home, audio):
    from meeting import paths
    paths.ensure_dirs()
    job = make_job()
    client = FakeClient(text="Pembicara 1: Halo semuanya.")
    meta = transcribe.transcribe_audio(audio, job, client=client)

    outdir = paths.output_dir() / job["id"]
    md = (outdir / "transcript.md").read_text(encoding="utf-8")
    txt = (outdir / "transcript.txt").read_text(encoding="utf-8")
    meta_json = json.loads((outdir / "meta.json").read_text(encoding="utf-8"))

    assert "Pembicara 1: Halo semuanya." in md
    assert "Pembicara 1: Halo semuanya." in txt
    # metadata header di .md: tanggal, durasi, device, model
    assert "2026-08-29T14:00:05" in md
    assert "1:01:01" in md
    assert "Headset (Jabra)" in md
    assert "gemini-flash" in md
    assert meta_json["status"] == "done"
    assert meta["status"] == "done"


def test_transcribe_polls_until_active(meeting_home, audio):
    from meeting import paths
    paths.ensure_dirs()
    client = FakeClient()
    transcribe.transcribe_audio(audio, make_job(), client=client)
    assert client.files.get_calls >= 2  # poll minimal sekali setelah PROCESSING


def test_transcribe_uploads_then_deletes_remote_file(meeting_home, audio):
    from meeting import paths
    paths.ensure_dirs()
    client = FakeClient()
    transcribe.transcribe_audio(audio, make_job(), client=client)
    assert len(client.files.uploaded) == 1
    assert client.files.deleted == ["files/fake-1"]


def test_transcribe_empty_text_raises_and_writes_nothing(meeting_home, audio):
    from meeting import paths
    paths.ensure_dirs()
    job = make_job()
    client = FakeClient(fail_generate=True)
    with pytest.raises(transcribe.TranscribeError):
        transcribe.transcribe_audio(audio, job, client=client)
    outdir = paths.output_dir() / job["id"]
    assert not outdir.exists()


def test_transcribe_requires_api_key_without_client(meeting_home, audio, monkeypatch):
    from meeting import config as cfg
    monkeypatch.setattr(cfg, "api_key", lambda: None)
    with pytest.raises(transcribe.TranscribeError, match="API key"):
        transcribe.transcribe_audio(audio, make_job(), client=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_transcribe.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/meeting/transcribe.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_transcribe.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/meeting/transcribe.py tests/test_transcribe.py
git commit -m "feat: Gemini transcription with File API upload/poll/delete and local output"
```

---

## Task 7: Worker — file-lock, FIFO loop, finalisasi + transkripsi + retry backoff

**Files:**
- Create: `src/meeting/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `queuer` (job schema), `finalizer.finalize/resolve_audio`, `transcribe.transcribe_audio`, `indexmd.upsert`, `paths.worker_log()`
- Produces:
  - `worker.main() -> int` — entry point `meeting-worker`; juga `python -m meeting.worker`.
  - `worker.run_once() -> bool` — ambil job tertua di `pending/` (sort nama file), rename ke `.processing`, proses, pindah JSON ke `done/`/`failed/` (dengan field `status`, `error`, `finished_at` di-update). Return False bila antrian kosong. **Satu worker aktif** via lock file `data/worker.lock` (`os.open` + `O_CREAT|O_EXCL`; exit 0 bila sudah ada worker; stale-lock: bila PID di lock tidak hidup, ambil alih).
  - `worker.process(job: dict, job_path: Path) -> None` — panggil `finalizer` bila `needs_finalization` (lalu set `job["audio"]` ke FLAC relatif & `needs_finalization=False`), `indexmd.upsert(status=pending→done/failed)` per transisi, backoff 429: 60 s lalu 300 s, maks 2 retry; error lain → langsung failed. Log semua ke `data/worker.log` (format `2026-08-29T14:00:05 [level] pesan`; **API key tidak pernah ditulis**).
  - API key invalid → raise khusus agar worker exit (bukan job failed): `worker.FatalWorkerError(RuntimeError)`, ditangkap `main()` → tulis log + buat file `data/worker.last_error` berisi pesan (dibaca `meeting status`).

- [ ] **Step 1: Write the failing test**

`tests/test_worker.py`:

```python
import json
import time

import pytest

from meeting import queuer, worker


def make_job(**over):
    job = {
        "id": "2026-08-29_1400-standup",
        "created_at": "2026-08-29T14:00:05",
        "source": "mic",
        "audio": "recordings/2026-08-29_1400-standup.flac",
        "wav": None,
        "needs_finalization": False,
        "duration_sec": 60.0,
        "device": "Headset (Jabra)",
        "model": "gemini-flash",
        "status": "pending",
    }
    job.update(over)
    return job


@pytest.fixture
def flac(meeting_home):
    """FLAC kecil final di recordings/ — job tanpa finalisasi."""
    import numpy as np
    import soundfile as sf
    from meeting import paths
    paths.ensure_dirs()
    p = paths.recordings_dir() / "2026-08-29_1400-standup.flac"
    t = np.linspace(0, 0.5, 8000, endpoint=False)
    sf.write(p, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000, format="FLAC")
    return p


class FakeTranscriber:
    """Transcriber yang bisa diprogram gagal N kali pertama."""

    def __init__(self, fail_times=0, error=RuntimeError("network down")):
        self.fail_times = fail_times
        self.calls = 0
        self.error = error

    def __call__(self, audio_path, job, client=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        return {"status": "done", "id": job["id"]}


def test_run_once_empty_queue_returns_false(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    assert worker.run_once() is False


def test_run_once_success_moves_job_to_done(meeting_home, flac, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())
    monkeypatch.setattr(worker, "transcribe_audio", FakeTranscriber())

    assert worker.run_once() is True
    done = list(paths.queue_dir("done").glob("*.json"))
    assert len(done) == 1
    job = json.loads(done[0].read_text(encoding="utf-8"))
    assert job["status"] == "done"
    assert not list(paths.queue_dir("pending").glob("*.json"))
    assert not list(paths.queue_dir("pending").glob("*.processing"))


def test_run_once_failure_moves_job_to_failed(meeting_home, flac, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())
    monkeypatch.setattr(
        worker, "transcribe_audio", FakeTranscriber(fail_times=99)
    )

    assert worker.run_once() is True
    failed = list(paths.queue_dir("failed").glob("*.json"))
    assert len(failed) == 1
    job = json.loads(failed[0].read_text(encoding="utf-8"))
    assert job["status"] == "failed"
    assert "network down" in job["error"]
    assert not flac.with_suffix(".deleted").exists()
    assert flac.exists()  # audio TIDAK dihapus saat gagal


def test_rate_limit_retries_with_backoff(meeting_home, flac, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())

    sleeps = []
    monkeypatch.setattr(worker.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(
        worker, "transcribe_audio", FakeTranscriber(fail_times=2)
    )

    class RateLimitError(Exception):
        pass

    # error pertama & kedua = 429-ish (punya attr .code == 429)
    fl = FakeTranscriber(fail_times=2)
    fl.error = worker.RateLimitedError("429 quota")
    monkeypatch.setattr(worker, "transcribe_audio", fl)

    assert worker.run_once() is True
    assert fl.calls == 3  # 2 gagal + 1 sukses
    assert sleeps == [60, 300]
    assert list(paths.queue_dir("done").glob("*.json"))


def test_rate_limit_gives_up_after_two_retries(meeting_home, flac, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())

    sleeps = []
    monkeypatch.setattr(worker.time, "sleep", lambda s: sleeps.append(s))
    fl = FakeTranscriber(fail_times=99)
    fl.error = worker.RateLimitedError("429 quota")
    monkeypatch.setattr(worker, "transcribe_audio", fl)

    worker.run_once()
    assert sleeps == [60, 300]
    job = json.loads(
        list(paths.queue_dir("failed").glob("*.json"))[0].read_text(encoding="utf-8")
    )
    assert "429" in job["error"]


def test_finalize_failure_routes_job_to_failed(meeting_home, monkeypatch):
    """WAV hilang saat finalisasi → job ke failed/ (bukan loop tanpa batas di pending)."""
    from meeting import paths
    paths.ensure_dirs()
    job = make_job(wav="tmp/tidak_ada.wav", needs_finalization=True)
    queuer.enqueue(job)

    calls = []
    monkeypatch.setattr(
        worker, "transcribe_audio",
        lambda audio_path, job, client=None: calls.append(1) or {"status": "done"},
    )
    assert worker.run_once() is True
    assert calls == []  # transkripsi tidak pernah dipanggil
    failed = json.loads(
        list(paths.queue_dir("failed").glob("*.json"))[0].read_text(encoding="utf-8")
    )
    assert failed["status"] == "failed"
    assert "tidak_ada" in failed["error"]
    assert not list(paths.queue_dir("pending").glob("*.json"))
    from meeting import indexmd
    assert indexmd.rows()[0]["status"] == "failed"


def test_run_once_finalizes_wav_before_transcribe(meeting_home, monkeypatch, tmp_path):
    from meeting import finalizer as fin
    from meeting import paths
    paths.ensure_dirs()

    import numpy as np
    import soundfile as sf
    wav = paths.tmp_dir() / "2026-08-29_1400-standup_mic.wav"
    t = np.linspace(0, 0.3, 4800, endpoint=False)
    sf.write(wav, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000, subtype="PCM_16")

    job = make_job(
        wav="tmp/2026-08-29_1400-standup_mic.wav", needs_finalization=True
    )
    queuer.enqueue(job)

    calls = []
    monkeypatch.setattr(
        worker,
        "transcribe_audio",
        lambda audio_path, job, client=None: calls.append(str(audio_path)) or {"status": "done"},
    )
    worker.run_once()

    assert calls and "recordings" in calls[0] and calls[0].endswith(".flac")
    assert not wav.exists()  # WAV dibersihkan setelah FLAC verify
    done_job = json.loads(
        list(paths.queue_dir("done").glob("*.json"))[0].read_text(encoding="utf-8")
    )
    assert done_job["needs_finalization"] is False


def test_index_updated_on_done(meeting_home, flac, monkeypatch):
    from meeting import indexmd, paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())
    monkeypatch.setattr(worker, "transcribe_audio", FakeTranscriber())
    worker.run_once()
    rows = indexmd.rows()
    assert rows and rows[0]["status"] == "done"


def test_index_updated_on_failed(meeting_home, flac, monkeypatch):
    from meeting import indexmd, paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())
    monkeypatch.setattr(worker, "transcribe_audio", FakeTranscriber(fail_times=99))
    worker.run_once()
    rows = indexmd.rows()
    assert rows and rows[0]["status"] == "failed"


def test_lock_single_worker(meeting_home, flac, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    lock = paths.home() / "data" / "worker.lock"
    lock.write_text("999999")  # PID tidak hidup → stale
    # stale lock dengan PID mati diambil alih
    assert worker._acquire_lock() is True
    assert worker._acquire_lock() is False  # kini hidup (PID kita)


def test_fatal_api_key_error_writes_last_error(meeting_home, flac, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    queuer.enqueue(make_job())

    fl = FakeTranscriber(fail_times=99)
    fl.error = worker.FatalWorkerError("API key invalid")
    monkeypatch.setattr(worker, "transcribe_audio", fl)

    with pytest.raises(worker.FatalWorkerError):
        worker.run_once()
    err = (paths.home() / "data" / "worker.last_error").read_text(encoding="utf-8")
    assert "API key invalid" in err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/meeting/worker.py`:

```python
"""Worker background: FIFO, satu aktif (lock), finalisasi → transkripsi → done/failed.
Di-spawn detached oleh queuer; exit saat antrian kosong — tidak ada proses menetap."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config, finalizer, indexmd, paths, queuer
from .transcribe import TranscribeError, transcribe_audio  # nama di-import agar test dapat monkeypatch worker.transcribe_audio

BACKOFF_SCHEDULE = (60.0, 300.0)  # 429: 60s lalu 5 menit, maks 2 retry


class RateLimitedError(RuntimeError):
    """429 / quota dari Gemini."""


class FatalWorkerError(RuntimeError):
    """Error yang mematikan worker (bukan job): API key invalid dll."""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _log(level: str, msg: str) -> None:
    line = f"{_now()} [{level}] {msg}\n"
    log = paths.worker_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(line)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) di Windows = CTRL_C_EVENT (bukan probe!) — pakai Win32 API.
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True  # EPERM di POSIX = proses ada, milik user lain
    except OSError:
        return False


def _acquire_lock() -> bool:
    lock = paths.home() / "data" / "worker.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            pid = int(lock.read_text().strip())
            if _pid_alive(pid):
                return False
        except (ValueError, OSError):
            pass  # korup → anggap stale
        lock.unlink(missing_ok=True)  # stale (PID mati/korup) → ambil alih
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_lock() -> None:
    (paths.home() / "data" / "worker.lock").unlink(missing_ok=True)


def _to_rate_limited(e: Exception) -> bool:
    code = getattr(e, "code", None)
    if code == 429 or "429" in str(e) or "quota" in str(e).lower():
        return True
    return isinstance(e, RateLimitedError)


def _upsert_index(job: dict, status: str) -> None:
    dur = job.get("duration_sec")
    indexmd.upsert(
        {
            "id": job["id"],
            "tanggal": (job.get("created_at") or "").replace("T", " "),
            "durasi": indexmd.fmt_duration(dur) if dur else "-",
            "status": status,
            "path": f"data/output/{job['id']}/transcript.md",
        }
    )


def _move_job(job_path: Path, dest_sub: str, job: dict) -> Path:
    job["finished_at"] = _now()
    # nama file tujuan pakai id (bukan job_path.name yang berakhiran .processing)
    dest = paths.queue_dir(dest_sub) / f"{job['id']}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
    job_path.unlink(missing_ok=True)
    return dest


def _fail_job(job: dict, job_path: Path, err: Exception) -> None:
    """Bawa job ke failed/ dengan bookkeeping lengkap (INDEX + error field). Audio tak disentuh."""
    job["status"] = "failed"
    job["error"] = f"{type(err).__name__}: {err}"
    _upsert_index(job, "failed")
    _move_job(job_path, "failed", job)
    _log("error", f"{job['id']} failed: {job['error']}")


def process(job: dict, job_path: Path) -> None:
    """Satu job: finalisasi (bila perlu) → transkripsi (dengan retry 429) → done/failed."""
    if job.get("needs_finalization"):
        # wav bisa string (mic/sys) atau list (both)
        try:
            raw = job["wav"]
            wav_list = [raw] if isinstance(raw, str) else list(raw)
            wavs = [
                Path(w) if Path(w).is_absolute() else paths.home() / "data" / w
                for w in wav_list
            ]
            flac = finalizer.finalize(wavs, job["id"])
        except Exception as e:
            _fail_job(job, job_path, e)
            return
        job["audio"] = str(flac.relative_to(paths.home() / "data"))
        job["needs_finalization"] = False

    _upsert_index(job, "pending")

    last_err: Exception | None = None
    for attempt in range(len(BACKOFF_SCHEDULE) + 1):
        try:
            transcribe_audio(finalizer.resolve_audio(job), job)
            _upsert_index(job, "done")
            job["status"] = "done"
            _move_job(job_path, "done", job)
            _log("info", f"{job['id']} done")
            return
        except FatalWorkerError as e:
            (paths.home() / "data" / "worker.last_error").write_text(
                str(e), encoding="utf-8"
            )
            _log("error", f"{job['id']} fatal: {e}")
            raise
        except TranscribeError as e:
            if "API key" in str(e):
                # key invalid = fatal worker, bukan job failure
                fe = FatalWorkerError(str(e))
                (paths.home() / "data" / "worker.last_error").write_text(
                    str(fe), encoding="utf-8"
                )
                _log("error", f"{job['id']} fatal: {fe}")
                raise fe from e
            last_err = e
            retryable = _to_rate_limited(e)
        except Exception as e:  # network, unexpected
            last_err = RateLimitedError(str(e)) if _to_rate_limited(e) else e
            retryable = _to_rate_limited(e)

        if not retryable:
            break  # error non-429 → langsung failed (spec: tidak ada backoff)
        if attempt < len(BACKOFF_SCHEDULE):
            _log("warn", f"{job['id']} attempt {attempt + 1} gagal ({last_err}); backoff")
            time.sleep(BACKOFF_SCHEDULE[attempt])

    assert last_err is not None
    _fail_job(job, job_path, last_err)


def _oldest_pending() -> Path | None:
    pend = sorted(paths.queue_dir("pending").glob("*.json"))
    return pend[0] if pend else None


def run_once() -> bool:
    """Proses satu job. Return False bila antrian kosong."""
    src = _oldest_pending()
    if src is None:
        return False
    processing = src.with_suffix(".json.processing")
    os.replace(src, processing)  # atomic claim
    try:
        job = json.loads(processing.read_text(encoding="utf-8"))
        process(job, processing)
    except FatalWorkerError:
        # kembalikan job ke pending agar retry setelah key diperbaiki
        os.replace(processing, src)
        raise
    except Exception as e:
        _log("error", f"job {src.stem} error tak terduga: {e}")
        os.replace(processing, src)  # job kembali ke pending utk run berikutnya
    return True


def main() -> int:
    paths.ensure_dirs()
    if not _acquire_lock():
        _log("info", "worker lain sudah aktif — exit")
        return 0
    try:
        while run_once():
            pass
    except FatalWorkerError as e:
        _log("error", f"worker exit fatal: {e}")  # last_error sudah ditulis process()
        return 1
    finally:
        _release_lock()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worker.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit**

```bash
git add src/meeting/worker.py tests/test_worker.py
git commit -m "feat: background worker with lock, FIFO, 429 backoff, finalize-before-transcribe"
```

---

## Task 8: Session lock + stop-flag — anti-dobel lintas proses

**Files:**
- Create: `src/meeting/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `paths.session_file()`, `paths.stop_flag()`
- Produces:
  - `session.start(meta: dict) -> None` — tulis `record_session.json` atomik berisi `{"pid": int, "started_at": ISO, "source": str, "id": str, "wav_paths": [str], "device": str}`. Raise `SessionActiveError(RuntimeError)` bila sesi aktif (PID hidup) — **pesan Indonesia untuk user**: `"Masih ada sesi rekam aktif (id ..., mulai ...). Hentikan dulu dengan meeting stop / klik tombol stop."`
  - `session.active() -> dict | None` — sesi aktif (PID hidup) atau None. PID mati → return None (yatim; file dibiarkan untuk recovery).
  - `session.request_stop() -> None` — touch `tmp/record.stop`.
  - `session.stop_requested() -> bool`.
  - `session.clear_stop_flag() -> None`.
  - `session.finish() -> dict` — baca session file, hapus file + stop flag, return meta (dipakai CLI setelah recorder berhenti untuk enqueue job). Raise `SessionActiveError` bila tidak ada file.
  - `session.SessionActiveError(RuntimeError)`.

- [ ] **Step 1: Write the failing test**

`tests/test_session.py`:

```python
import os

import pytest

from meeting import session


def test_start_writes_session_file(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    session.start({"source": "mic", "id": "2026-08-29_1400", "wav_paths": ["tmp/a.wav"], "device": "Mic"})
    data = session.active()
    assert data["pid"] == os.getpid()
    assert data["source"] == "mic"
    assert data["id"] == "2026-08-29_1400"


def test_second_start_rejected_while_active(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    session.start({"source": "mic", "id": "a", "wav_paths": [], "device": "d"})
    with pytest.raises(session.SessionActiveError, match="sesi rekam aktif"):
        session.start({"source": "sys", "id": "b", "wav_paths": [], "device": "d"})


def test_stale_session_not_active(meeting_home):
    """PID mati = yatim → active() None, start() boleh."""
    from meeting import paths
    paths.ensure_dirs()
    paths.session_file().write_text('{"pid": 999999, "id": "x", "source": "mic", "wav_paths": [], "device": "d", "started_at": "2026-08-29T14:00:00"}', encoding="utf-8")
    assert session.active() is None
    session.start({"source": "mic", "id": "y", "wav_paths": [], "device": "d"})  # tidak raise


def test_stop_flag_roundtrip(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    assert session.stop_requested() is False
    session.request_stop()
    assert session.stop_requested() is True
    session.clear_stop_flag()
    assert session.stop_requested() is False


def test_finish_returns_meta_and_cleans_up(meeting_home):
    from meeting import paths
    paths.ensure_dirs()
    session.start({"source": "mic", "id": "z", "wav_paths": ["tmp/z.wav"], "device": "d"})
    session.request_stop()
    meta = session.finish()
    assert meta["id"] == "z"
    assert not paths.session_file().exists()
    assert not paths.stop_flag().exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_session.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/meeting/session.py`:

```python
"""Session-file lock: satu sesi rekam aktif lintas proses (CLI + floating button).
PID mati = yatim → bukan aktif; file dibiarkan utk recovery (recover.py)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from . import paths


class SessionActiveError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) di Windows = CTRL_C_EVENT (bukan probe!) — pakai Win32 API.
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True  # EPERM di POSIX = proses ada, milik user lain
    except OSError:
        return False


def _read() -> dict | None:
    p = paths.session_file()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def active() -> dict | None:
    data = _read()
    if data and _pid_alive(int(data.get("pid", 0))):
        return data
    return None


def orphaned() -> dict | None:
    """Sesi yatim: file ada, PID mati."""
    data = _read()
    if data and not _pid_alive(int(data.get("pid", 0))):
        return data
    return None


def start(meta: dict) -> None:
    act = active()
    if act:
        raise SessionActiveError(
            f"Masih ada sesi rekam aktif (id {act.get('id')}, mulai "
            f"{act.get('started_at')}). Hentikan dulu dengan meeting stop "
            f"/ klik tombol stop."
        )
    data = dict(meta)
    data["pid"] = os.getpid()
    data["started_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    p = paths.session_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    clear_stop_flag()


def request_stop() -> None:
    paths.ensure_dirs()
    paths.stop_flag().touch()


def stop_requested() -> bool:
    return paths.stop_flag().exists()


def clear_stop_flag() -> None:
    paths.stop_flag().unlink(missing_ok=True)


def finish() -> dict:
    data = _read()
    if data is None:
        raise SessionActiveError("tidak ada sesi rekam untuk diselesaikan")
    paths.session_file().unlink(missing_ok=True)
    clear_stop_flag()
    return data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_session.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/meeting/session.py tests/test_session.py
git commit -m "feat: cross-process session lock and stop flag"
```

---

## Task 9: Recorder — WAV incremental mic/sys/both

**Files:**
- Create: `src/meeting/recorder.py`
- Test: `tests/test_recorder.py`

**Interfaces:**
- Consumes: `session` (stop flag), `paths.tmp_dir()`, `config.load()`
- Produces:
  - `recorder.record(source: str, job_id: str, mic_device: str | None = None) -> dict` — blocking hingga stop; return meta `{"duration_sec": float, "wav_paths": [Path, ...], "device": str}`.
  - Backend mic: `sounddevice.InputStream` 16 kHz mono float32, callback menulis chunk; **WAV incremental**: header 44-byte ditulis di awal, setiap chunk `seek`+append lalu patch header RIFF/data size di akhir tiap flush (~5 s) — file valid walau crash. Gunakan `soundfile` dalam mode block: buka `sf.SoundFile(..., mode="w")` dan panggil `.flush()` tiap ~5 detik — ini cara resmi crash-safe (libsndfile menulis frame yang sudah ditulis; header dipatch saat flush).
  - Backend sys (loopback): `soundcard` — `sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)` buka `recorder.record(samplerate=16000, blocksize=8000, channels=1, exclusive_mode=False)`; loop baca + tulis `SoundFile` + cek stop flag.
  - `--both`: dua thread (mic via sounddevice callback stream; sys via soundcard loop loop thread), stop flag dicek kedua loop; join keduanya.
  - Device dicabut (`sd.PortAudioError`, `sc._SoundCardRuntimeError`/OSError) → stop rapi: tutup stream, flush, return meta dengan `device_lost=True`; CLI memperlakukan sama seperti stop normal (tetap enqueue).
  - Import `sounddevice`/`soundcard` **lazy di dalam fungsi** (WSL test tidak punya lib).
  - Disk-full check sebelum mulai: `recorder.check_disk(wav_dir: Path, est_hours: float, streams: int) -> None` — raise `RecorderError` bila free < `115 MB/jam * streams * est_hours` (pakai `shutil.disk_usage`); CLI menangkap dan menampilkan.

- [ ] **Step 1: Write the failing test**

`tests/test_recorder.py` — backend di-mock penuh; test logika orkestrasi, bukan hardware:

```python
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from meeting import recorder


class FakeSD:
    """Sounddevice stub: callback stream yang menghasilkan 0.5 s sine lalu berhenti."""

    class InputStream:
        def __init__(self, samplerate, channels, dtype, callback, device=None):
            self.cb = callback

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def start(self):
            t = np.linspace(0, 0.5, 8000, endpoint=False)
            data = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32).reshape(-1, 1)
            self.cb(data, 8000, None, 0.0)

    class PortAudioError(Exception):
        pass

    @staticmethod
    def query_devices():
        return [{"name": "Default Mic"}]

    default = staticmethod(lambda: 0)


@pytest.fixture
def fake_sd(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "sounddevice", FakeSD)
    return FakeSD


def test_record_mic_writes_incremental_wav(meeting_home, monkeypatch, fake_sd):
    from meeting import paths, session
    paths.ensure_dirs()
    # stop setelah 0.1 s
    monkeypatch.setattr(recorder.time, "sleep", lambda s: session.request_stop())

    meta = recorder.record("mic", "2026-08-29_1400")
    assert meta["wav_paths"], "harus menghasilkan WAV"
    wav = Path(meta["wav_paths"][0])
    assert wav.exists()
    data, sr = sf.read(wav)
    assert sr == 16000
    assert len(data) == 8000  # 0.5 s dari fake stream
    assert meta["duration_sec"] > 0


def test_record_sys_uses_loopback(meeting_home, monkeypatch):
    from meeting import paths, session
    paths.ensure_dirs()
    monkeypatch.setattr(recorder.time, "sleep", lambda s: session.request_stop())

    class FakeMic:
        def recorder(self, *, samplerate, blocksize, channels, exclusive_mode):
            class Rec:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

                def __iter__(self):
                    t = np.linspace(0, 0.25, 4000, endpoint=False)
                    yield (0.1 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)

            return Rec()

    class FakeSC:
        @staticmethod
        def default_speaker():
            class S:
                name = "Speakers (Realtek)"
            return S()

        @staticmethod
        def get_microphone(id, include_loopback):
            assert include_loopback is True
            return FakeMic()

    import sys
    monkeypatch.setitem(sys.modules, "soundcard", FakeSC)
    meta = recorder.record("sys", "2026-08-29_1400")
    assert meta["device"] == "Speakers (Realtek) loopback"
    wav = Path(meta["wav_paths"][0])
    data, sr = sf.read(wav)
    assert sr == 16000 and len(data) == 4000


def test_record_both_writes_two_wavs_min_duration(meeting_home, monkeypatch, fake_sd):
    """--both: dua thread, dua WAV; durasi = min(mic, sys); device_lost False."""
    from meeting import paths, session
    paths.ensure_dirs()
    monkeypatch.setattr(recorder.time, "sleep", lambda s: session.request_stop())

    class FakeMic:
        def recorder(self, *, samplerate, blocksize, channels, exclusive_mode):
            class Rec:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

                def __iter__(self):
                    t = np.linspace(0, 0.25, 4000, endpoint=False)
                    yield (0.1 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)

            return Rec()

    class FakeSC:
        @staticmethod
        def default_speaker():
            class S:
                name = "Speakers (Realtek)"
            return S()

        @staticmethod
        def get_microphone(id, include_loopback):
            return FakeMic()

    import sys as _s
    monkeypatch.setitem(_s.modules, "soundcard", FakeSC)

    meta = recorder.record("both", "2026-08-29_1400")
    assert meta["device_lost"] is False
    assert len(meta["wav_paths"]) == 2  # mic + sys
    frames = sorted(sf.info(w).frames for w in meta["wav_paths"])
    assert frames == [4000, 8000]  # sys 0.25 s, mic 0.5 s
    assert meta["duration_sec"] == pytest.approx(0.25)  # min(mic, sys)


def test_record_both_worker_crash_sets_device_lost(meeting_home, monkeypatch, fake_sd):
    """Worker mic mati karena error non-_Stop (mis. import) → errs terisi, lost True, tidak hang."""
    from meeting import paths, session
    paths.ensure_dirs()

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(recorder, "_record_mic", boom)

    class FakeMic:
        def recorder(self, *, samplerate, blocksize, channels, exclusive_mode):
            class Rec:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

                def __iter__(self):
                    t = np.linspace(0, 0.25, 4000, endpoint=False)
                    yield (0.1 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)

            return Rec()

    class FakeSC:
        @staticmethod
        def default_speaker():
            class S:
                name = "Speakers (Realtek)"
            return S()

        @staticmethod
        def get_microphone(id, include_loopback):
            return FakeMic()

    import sys as _s
    monkeypatch.setitem(_s.modules, "soundcard", FakeSC)

    meta = recorder.record("both", "2026-08-29_1400")
    assert meta["device_lost"] is True  # kegagalan worker tidak lagi "laundry" jadi sukses
    assert paths.tmp_dir().joinpath("2026-08-29_1400_sys.wav").exists()  # sys tetap terekam


def test_record_device_lost_stops_clean(meeting_home, monkeypatch, fake_sd):
    """Stream mati di tengah (0.25 s sudah terekam) → return meta rapi, WAV valid."""
    from meeting import paths
    paths.ensure_dirs()

    class DyingStream(FakeSD.InputStream):
        def start(self):
            # 0.25 s normal dulu…
            t = np.linspace(0, 0.25, 4000, endpoint=False)
            data = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32).reshape(-1, 1)
            self.cb(data, 4000, None, 0.0)
            # …lalu device dicabut
            raise FakeSD.PortAudioError("device unplugged")

    monkeypatch.setattr(FakeSD, "InputStream", DyingStream)

    meta = recorder.record("mic", "2026-08-29_1400")
    assert meta["device_lost"] is True
    wav = Path(meta["wav_paths"][0])
    assert wav.exists()
    data, sr = sf.read(wav)
    assert sr == 16000 and len(data) == 4000  # audio sampai titik mati selamat
    assert meta["duration_sec"] == pytest.approx(0.25)


def test_check_disk_insufficient(meeting_home, monkeypatch):
    import shutil
    monkeypatch.setattr(recorder.shutil, "disk_usage", lambda p: type("DU", (), {"free": 10 * 1024 * 1024})())
    with pytest.raises(recorder.RecorderError, match="ruang disk"):
        recorder.check_disk(meeting_home, est_hours=1.0, streams=1)


def test_check_disk_ok(meeting_home, monkeypatch):
    import shutil
    monkeypatch.setattr(
        recorder.shutil,
        "disk_usage",
        lambda p: type("DU", (), {"free": 500 * 1024 * 1024})(),
    )
    recorder.check_disk(meeting_home, est_hours=1.0, streams=1)  # tidak raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_recorder.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/meeting/recorder.py`:

```python
"""Rekam mic (sounddevice) / loopback (soundcard) / keduanya → WAV incremental 16k.
Backend di-import lazy; stop-flag file = sinyal stop lintas proses (CLI & tombol)."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from . import paths, session

SAMPLE_RATE = 16000
BYTES_PER_HOUR_MONO_16BIT = 115 * 1024 * 1024  # ≈ 32 kB/s


class RecorderError(RuntimeError):
    pass


def check_disk(wav_dir: Path, est_hours: float, streams: int) -> None:
    free = shutil.disk_usage(wav_dir).free
    need = BYTES_PER_HOUR_MONO_16BIT * streams * max(est_hours, 0.1)
    if free < need:
        raise RecorderError(
            f"ruang disk tidak cukup: butuh ≈{int(need / 1024 / 1024)} MB, "
            f"tersedia {int(free / 1024 / 1024)} MB. Kosongkan disk atau pakai durasi lebih pendek."
        )


def _writer(path: Path) -> sf.SoundFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    return sf.SoundFile(str(path), mode="w", samplerate=SAMPLE_RATE,
                        channels=1, subtype="PCM_16", format="WAV")


class _Stop(Exception):
    pass


def _record_mic(job_id: str, wav: Path, mic_device: str | None) -> float:
    """sounddevice blocking stream; return durasi detik. Raise _Stop bila device hilang."""
    import sounddevice as sd

    frames = [0]

    def callback(indata, frame_count, time_info, status):
        f.write(indata.copy())
        f.flush()  # patch header — file selalu valid
        frames[0] += frame_count

    f = _writer(wav)
    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            callback=callback, device=mic_device,
        ) as stream:
            stream.start()
            while not session.stop_requested():
                time.sleep(0.2)
    except sd.PortAudioError as e:
        f.close()
        raise _Stop(f"mic device error: {e}") from e
    finally:
        try:
            f.flush()
        except Exception:
            pass
        f.close()
    return frames[0] / SAMPLE_RATE


def _record_sys(job_id: str, wav: Path) -> float:
    """soundcard WASAPI loopback; return durasi detik."""
    import soundcard as sc

    spk = sc.default_speaker()
    mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
    f = _writer(wav)
    n = 0
    try:
        with mic.recorder(samplerate=SAMPLE_RATE, blocksize=8000,
                          channels=1, exclusive_mode=False) as rec:
            for block in rec:
                f.write(np.asarray(block, dtype=np.float32).reshape(-1, 1))
                f.flush()
                n += len(block)
                if session.stop_requested():
                    break
    except (OSError, Exception) as e:  # termasuk device dicabut
        raise _Stop(f"loopback error: {e}") from e
    finally:
        try:
            f.flush()
        except Exception:
            pass
        f.close()
    return n / SAMPLE_RATE


def record(source: str, job_id: str, mic_device: str | None = None) -> dict:
    """Rekam hingga stop-flag. Return meta utk session/CLI."""
    paths.ensure_dirs()
    tmp = paths.tmp_dir()
    device = "mic default"
    lost = False

    if source == "mic":
        wav = tmp / f"{job_id}_mic.wav"
        try:
            dur = _record_mic(job_id, wav, mic_device)
        except _Stop as e:
            lost = True
            dur = _frames_of(wav) / SAMPLE_RATE
            device = str(e)
        if not lost:
            device = mic_device or "mic default"
        wavs = [wav]
    elif source == "sys":
        wav = tmp / f"{job_id}_sys.wav"
        try:
            dur = _record_sys(job_id, wav)
        except _Stop as e:
            lost = True
            dur = _frames_of(wav) / SAMPLE_RATE
        import sys as _s
        if "soundcard" in _s.modules:
            device = f"{_s.modules['soundcard'].default_speaker().name} loopback"
        wavs = [wav]
    elif source == "both":
        wav_m = tmp / f"{job_id}_mic.wav"
        wav_s = tmp / f"{job_id}_sys.wav"
        errs: list[Exception] = []

        def run_mic():
            try:
                _record_mic(job_id, wav_m, mic_device)
            except Exception as e:  # _Stop, disk-full, import gagal — semua berhenti rapi
                errs.append(e)

        def run_sys():
            try:
                _record_sys(job_id, wav_s)
            except Exception as e:
                errs.append(e)

        tm = threading.Thread(target=run_mic)
        ts = threading.Thread(target=run_sys)
        tm.start(); ts.start()
        while (tm.is_alive() or ts.is_alive()) and not session.stop_requested() and not errs:
            time.sleep(0.2)
        if errs:
            lost = True
            session.request_stop()  # satu stream mati → hentikan juga satunya
            tm.join(timeout=5); ts.join(timeout=5)
        else:
            tm.join(); ts.join()
        dur = min(_frames_of(wav_m), _frames_of(wav_s)) / SAMPLE_RATE or 0
        wavs = [wav_m, wav_s]
    else:
        raise RecorderError(f"sumber tidak dikenal: {source!r} (mic|sys|both)")

    wavs = [w for w in wavs if w.exists() and _frames_of(w) > 0]
    return {
        "duration_sec": dur,
        "wav_paths": wavs,
        "device": device,
        "device_lost": lost,
    }


def _frames_of(wav: Path) -> int:
    try:
        return sf.info(wav).frames
    except Exception:
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_recorder.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/meeting/recorder.py tests/test_recorder.py
git commit -m "feat: incremental WAV recorder for mic, loopback, and both"
```

---

## Task 10: Recovery — sesi yatim

**Files:**
- Create: `src/meeting/recover.py`
- Test: `tests/test_recover.py`

**Interfaces:**
- Consumes: `session.orphaned()`, `finalizer`, `queuer`, `indexmd`
- Produces: `recover.find_orphans() -> list[dict]` — scan `tmp/*_mic.wav`/`*_sys.wav` + `record_session.json` yatim; kembalikan meta. `recover.offer_recovery(ui_confirm=lambda msg: bool) -> dict | None` — jika ada yatim: prompt via callback (CLI pakai `input()`, button pakai `messagebox`); bila dikonfirmasi → finalisasi + enqueue job + INDEX upsert; bila ditolak → **pindahkan WAV ke `tmp/discarded/`** (bukan hapus — prinsip audio tidak dihapus tool) + hapus session file yatim. Return job dict atau None.

- [ ] **Step 1: Write the failing test**

`tests/test_recover.py`:

```python
import json

import numpy as np
import soundfile as sf

from meeting import recover


def write_wav(p, seconds=0.3):
    p.parent.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0, seconds, int(16000 * seconds), endpoint=False)
    sf.write(p, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000, subtype="PCM_16")
    return p


def orphan_session(meeting_home, pid=999999):
    from meeting import paths
    paths.ensure_dirs()
    wav = write_wav(paths.tmp_dir() / "2026-08-28_0900_mic.wav")
    paths.session_file().write_text(
        json.dumps({
            "pid": pid, "id": "2026-08-28_0900", "source": "mic",
            "started_at": "2026-08-28T09:00:00", "device": "mic default",
            "wav_paths": [str(wav)],
        }),
        encoding="utf-8",
    )
    return wav


def test_find_orphans_detects_yatim(meeting_home):
    orphan_session(meeting_home)
    orphans = recover.find_orphans()
    assert len(orphans) == 1
    assert orphans[0]["id"] == "2026-08-28_0900"


def test_no_orphan_when_session_active(meeting_home):
    import os
    orphan_session(meeting_home, pid=os.getpid())  # PID hidup = sesi aktif
    assert recover.find_orphans() == []


def test_offer_recovery_confirmed_finalizes_and_enqueues(meeting_home, monkeypatch):
    from meeting import paths, queuer
    orphan_session(meeting_home)
    monkeypatch.setattr(queuer, "spawn_worker", lambda: None)  # jangan spawn worker nyata (race dgn assert)

    job = recover.offer_recovery(ui_confirm=lambda msg: True)
    assert job is not None and job["id"] == "2026-08-28_0900"
    # FLAC final ada, WAV dibersihkan oleh finalizer
    assert (paths.recordings_dir() / "2026-08-28_0900.flac").exists()
    pend = list(paths.queue_dir("pending").glob("*.json"))
    assert len(pend) == 1
    assert not paths.session_file().exists()


def test_offer_recovery_declined_moves_to_discarded(meeting_home):
    from meeting import paths
    wav = orphan_session(meeting_home)

    job = recover.offer_recovery(ui_confirm=lambda msg: False)
    assert job is None
    assert (paths.tmp_dir() / "discarded" / wav.name).exists()
    assert not wav.exists()
    assert not paths.session_file().exists()


def test_offer_recovery_no_orphan_returns_none(meeting_home):
    assert recover.offer_recovery(ui_confirm=lambda msg: True) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_recover.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/meeting/recover.py`:

```python
"""Deteksi & pemulihan sesi yatim (crash/force-quit): WAV utuh → tawarkan finalisasi
atau simpan ke discarded/ (tidak pernah hapus diam-diam)."""
from __future__ import annotations

import json
import shutil

from . import finalizer, indexmd, paths, queuer, session


def find_orphans() -> list[dict]:
    """Sesi yatim = session file dgn PID mati. WAV dicocokkan dari session file."""
    orphan = session.orphaned()
    if orphan:
        return [orphan]
    return []


def offer_recovery(ui_confirm) -> dict | None:
    orphans = find_orphans()
    if not orphans:
        return None
    meta = orphans[0]
    dur = _frames(meta) / 16000  # hitung SEBELUM finalize — finalize menghapus WAV
    durasi = indexmd.fmt_duration(dur)
    msg = (
        f"Ditemukan rekaman yatim dari sesi yang tidak selesai (id {meta['id']}, "
        f"mulai {meta.get('started_at')}, ≈{durasi}). Finalisasi & transkripsi sekarang? "
        f"[y/N]"
    )
    if not ui_confirm(msg):
        # tolak → pindahkan ke discarded/, JANGAN hapus
        disc = paths.tmp_dir() / "discarded"
        disc.mkdir(parents=True, exist_ok=True)
        for w in _wav_paths(meta):
            if w.exists():
                shutil.move(str(w), disc / w.name)
        paths.session_file().unlink(missing_ok=True)
        return None

    wavs = [w for w in _wav_paths(meta) if w.exists()]
    flac = finalizer.finalize(wavs, meta["id"])
    job = {
        "id": meta["id"],
        "created_at": meta.get("started_at") or "unknown",
        "source": meta.get("source", "mic"),
        "audio": str(flac.relative_to(paths.home() / "data")),
        "wav": None,
        "original_path": None,
        "needs_finalization": False,
        "duration_sec": dur,
        "device": meta.get("device"),
        "model": _current_model(),
        "status": "pending",
    }
    queuer.enqueue(job)
    indexmd.upsert(
        {
            "id": job["id"],
            "tanggal": (job["created_at"] or "").replace("T", " "),
            "durasi": indexmd.fmt_duration(job["duration_sec"]),
            "status": "pending",
            "path": f"data/output/{job['id']}/transcript.md",
        }
    )
    paths.session_file().unlink(missing_ok=True)
    queuer.spawn_worker()
    return job


def _wav_paths(meta: dict) -> list:
    from pathlib import Path as _P

    out = []
    for w in meta.get("wav_paths", []):
        p = _P(w)
        out.append(p if p.is_absolute() else paths.home() / "data" / w)
    return out


def _frames(meta: dict) -> int:
    import soundfile as sf

    total = 0
    for w in _wav_paths(meta):
        if w.exists():
            try:
                total = max(total, sf.info(w).frames)
            except Exception:
                pass
    return total


def _current_model() -> str:
    from . import config

    return config.load().get("model", "gemini-flash")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_recover.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/meeting/recover.py tests/test_recover.py
git commit -m "feat: orphan session recovery with confirm callback, discard-not-delete"
```

---

## Task 11: Devices — listing mic + loopback

**Files:**
- Create: `src/meeting/devices.py`
- Test: `tests/test_cli.py` (bagian devices, via monkeypatch sys.modules)

**Interfaces:**
- Consumes: `config.update(mic_device=...)`
- Produces: `devices.list_devices() -> list[dict]` — `{"kind": "mic"|"loopback", "name": str, "index": int}`; mic dari `sounddevice.query_devices()` (input only), loopback dari `soundcard.all_microphones(include_loopback=True)` dengan `isloopback`; `devices.set_mic(name: str) -> None` — simpan `mic_device` ke config; `devices.set_mic(None)` = kembali ke default Windows. Import lazy (sama seperti recorder).

- [ ] **Step 1: Write the failing test**

Tambahkan ke `tests/test_cli.py` (file dibuat penuh di Task 12; buat dulu bagian ini):

```python
# tests/test_cli.py — bagian devices (file dilengkapi di Task 12)
import json

from meeting import devices


class FakeSD:
    @staticmethod
    def query_devices():
        return [
            {"name": "Laptop Mic", "max_input_channels": 2, "max_output_channels": 0, "index": 0},
            {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2, "index": 1},
            {"name": "Jabra Headset", "max_input_channels": 1, "max_output_channels": 1, "index": 2},
        ]


class FakeLoopMic:
    def __init__(self, name, isloopback):
        self.name = name
        self.isloopback = isloopback


class FakeSC:
    @staticmethod
    def all_microphones(include_loopback=False):
        assert include_loopback is True
        return [
            FakeLoopMic("Laptop Mic", False),
            FakeLoopMic("Jabra Headset", False),
            FakeLoopMic("Speaker (Realtek) Loopback", True),
        ]


def test_list_devices_mics_and_loopback(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "sounddevice", FakeSD)
    monkeypatch.setitem(sys.modules, "soundcard", FakeSC)
    result = devices.list_devices()
    names = [(d["kind"], d["name"]) for d in result]
    assert ("mic", "Laptop Mic") in names
    assert ("mic", "Jabra Headset") in names
    assert ("loopback", "Speaker (Realtek) Loopback") in names
    assert ("mic", "Speakers") not in names  # output-only excluded
    assert ("loopback", "Laptop Mic") not in names


def test_set_mic_persists_config(monkeypatch, meeting_home):
    devices.set_mic("Jabra Headset")
    from meeting import config
    assert config.load()["mic_device"] == "Jabra Headset"
    devices.set_mic(None)
    assert config.load()["mic_device"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/meeting/devices.py`:

```python
"""Listing & pemilihan device. Backend lazy — modul bisa diimport tanpa audio lib."""
from __future__ import annotations

from . import config


def list_devices() -> list[dict]:
    result: list[dict] = []
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            result.append({"kind": "mic", "name": d["name"], "index": i})

    import soundcard as sc

    for m in sc.all_microphones(include_loopback=True):
        if getattr(m, "isloopback", False):
            result.append({"kind": "loopback", "name": m.name, "index": None})
    return result


def set_mic(name: str | None) -> None:
    """None = ikut default Windows."""
    config.update(mic_device=name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/meeting/devices.py tests/test_cli.py
git commit -m "feat: device listing (mic + WASAPI loopback) and mic selection"
```

---

## Task 12: CLI — record / transcribe / status / list / retry / devices

**Files:**
- Create: `src/meeting/cli.py`
- Modify: `tests/test_cli.py` (extend)
- Create: `README.md`

**Interfaces:**
- Consumes: semua modul sebelumnya; entry `cli.main(argv: list[str] | None = None) -> int`
- Produces: perintah sesuai spec:

```
meeting record [--sys|--mic|--both] [--name N]     # default = config.default_source
meeting transcribe FILE                            # mp4/mp3/m4a/flac/wav langsung; ffmpeg fallback utk format lain
meeting status                                     # pending/done/failed + sesi aktif + worker.last_error
meeting list                                       # dari INDEX.md, terbaru di atas
meeting retry                                      # failed/ → pending/, spawn worker
meeting devices [--mic NAME|none]                  # listing / set mic
meeting button                                     # Task 13 (dispatch ke button.run())
```

Perilaku penting yang diuji:
1. `record` tanpa flag → pakai `config["default_source"]`; dengan flag → jalankan `config.update(default_source=...)` (mode terakhir diingat).
2. Anti-dobel: `session.start` raise → tampilkan pesan + exit 1.
3. Setelah recorder berhenti (stop/flag/device-lost/Ctrl+C): `session.finish()` → build job (`needs_finalization=True` untuk record; WAV path relatif `data/`) → `queuer.enqueue` → INDEX upsert pending → `queuer.spawn_worker()` → print path job; **terminal bebas** (finalisasi FLAC di worker).
4. `transcribe FILE`: ekstensi didukung (`mp4|mp3|m4a|flac|wav|webm|ogg`) → job `needs_finalization=False`, `audio=<path absolut FILE>`, copy **tidak** dilakukan (file asli tidak pernah diubah/dihapus); tidak didukung → cek `ffmpeg` di PATH (`shutil.which`); ada → konversi ke `tmp/<id>.<ext>.flac` (mono 16k via `-ac 1 -ar 16000`) lalu job audio = FLAC hasil konversi; tidak ada → pesan error satu kalimat cara install (`winget install ffmpeg`).
5. Ctrl+C saat record → tangkap `KeyboardInterrupt` → set stop flag → recorder selesai rapi → enqueue (sama seperti stop normal).
6. `status`: hitung file di `queue/pending` (termasuk `.processing`), `done`, `failed`; tampilkan sesi aktif bila ada; tampilkan isi `data/worker.last_error` bila ada (lalu **tidak** menghapus — worker yang menghapus saat sukses berikutnya; sebenarnya: worker menghapus saat `main()` exit sukses).
7. `retry`: pindah semua `failed/*.json` → `pending/`, reset field `status="pending"`, hapus `error`/`finished_at`, spawn worker; laporan N job.
8. Exit code: sukses 0; error user (dobel, file tidak ada, disk) 1.

- [ ] **Step 1: Write the failing test**

Extend `tests/test_cli.py`:

```python
# ... lanjutan test_cli.py (setelah bagian devices Task 11)
import json
from pathlib import Path

import pytest

from meeting import cli, config, paths, queuer, session


@pytest.fixture
def fake_record(monkeypatch):
    """recorder.record → segera tulis WAV dummy dan kembalikan meta."""
    import numpy as np
    import soundfile as sf

    def _record(source, job_id, mic_device=None):
        paths.ensure_dirs()
        wav = paths.tmp_dir() / f"{job_id}_mic.wav"
        t = np.linspace(0, 0.2, 3200, endpoint=False)
        sf.write(wav, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000, subtype="PCM_16")
        return {"duration_sec": 0.2, "wav_paths": [wav], "device": "fake", "device_lost": False}

    monkeypatch.setattr(cli.recorder, "record", _record)
    return _record


def test_record_default_uses_config_source(meeting_home, fake_record, monkeypatch, capsys):
    monkeypatch.setattr(cli.queuer, "spawn_worker", lambda: None)
    monkeypatch.setattr(cli.time, "strftime", lambda fmt: "2026-08-29T14:00:00")
    rc = cli.main(["record"])
    assert rc == 0
    job_files = list(paths.queue_dir("pending").glob("*.json"))
    assert len(job_files) == 1
    job = json.loads(job_files[0].read_text(encoding="utf-8"))
    assert job["source"] == "mic"          # default config
    assert job["needs_finalization"] is True
    assert config.load()["default_source"] == "mic"


def test_record_sys_flag_updates_config(meeting_home, fake_record, monkeypatch):
    monkeypatch.setattr(cli.queuer, "spawn_worker", lambda: None)
    cli.main(["record", "--sys"])
    assert config.load()["default_source"] == "sys"
    job = json.loads(list(paths.queue_dir("pending").glob("*.json"))[0].read_text(encoding="utf-8"))
    assert job["source"] == "sys"


def test_record_anti_double_start(meeting_home, monkeypatch, capsys):
    from meeting import session as sess
    sess.start({"source": "mic", "id": "x", "wav_paths": [], "device": "d"})
    monkeypatch.setattr(cli.queuer, "spawn_worker", lambda: None)
    rc = cli.main(["record"])
    assert rc == 1
    err = capsys.readouterr().err  # pesan error user-facing → stderr
    assert "sesi rekam aktif" in err


def test_record_enqueue_after_stop(meeting_home, fake_record, monkeypatch):
    monkeypatch.setattr(cli.queuer, "spawn_worker", lambda: None)
    rc = cli.main(["record", "--name", "standup"])
    assert rc == 0
    job = json.loads(list(paths.queue_dir("pending").glob("*.json"))[0].read_text(encoding="utf-8"))
    assert job["id"].endswith("standup")
    assert job["needs_finalization"] is True
    assert job["wav"] == str(paths.tmp_dir() / f"{job['id']}_mic.wav").replace(str(paths.home() / "data") + "/", "tmp/")
    assert not paths.session_file().exists()  # session dibersihkan
    from meeting import indexmd
    assert indexmd.rows()[0]["status"] == "pending"


def test_transcribe_supported_extension_direct(meeting_home, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.queuer, "spawn_worker", lambda: None)
    src = tmp_path / "Zoom-2026-08-28.mp4"
    src.write_bytes(b"\x00" * 16)
    rc = cli.main(["transcribe", str(src)])
    assert rc == 0
    job = json.loads(list(paths.queue_dir("pending").glob("*.json"))[0].read_text(encoding="utf-8"))
    assert job["source"] == "file"
    assert job["original_path"] == str(src)
    assert job["needs_finalization"] is False
    assert src.exists()  # file asli tak tersentuh


def test_transcribe_unsupported_without_ffmpeg(meeting_home, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda x: None)
    src = tmp_path / "old.ape"
    src.write_bytes(b"\x00")
    rc = cli.main(["transcribe", str(src)])
    assert rc == 1
    assert "ffmpeg" in capsys.readouterr().err  # pesan error → stderr


def test_status_counts_and_last_error(meeting_home, capsys):
    paths.ensure_dirs()
    queuer.enqueue({"id": "a", "status": "pending"})
    (paths.home() / "data" / "worker.last_error").write_text("API key invalid", encoding="utf-8")
    rc = cli.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pending: 1" in out
    assert "API key invalid" in out


def test_retry_moves_failed_back(meeting_home, monkeypatch, capsys):
    monkeypatch.setattr(cli.queuer, "spawn_worker", lambda: None)
    paths.ensure_dirs()
    (paths.queue_dir("failed") / "j1.json").write_text(
        json.dumps({"id": "j1", "status": "failed", "error": "x", "finished_at": "t"}), encoding="utf-8"
    )
    rc = cli.main(["retry"])
    assert rc == 0
    job = json.loads((paths.queue_dir("pending") / "j1.json").read_text(encoding="utf-8"))
    assert job["status"] == "pending"
    assert "error" not in job
    assert not list(paths.queue_dir("failed").glob("*.json"))
    assert "1" in capsys.readouterr().out


def test_list_reads_index(meeting_home, capsys):
    from meeting import indexmd
    indexmd.upsert({"id": "m1", "tanggal": "2026-08-29 14:00", "durasi": "0:30", "status": "done", "path": "p"})
    rc = cli.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "m1" in out and "done" in out
```

- [ ] **Step  `2`: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'cli'`

- [ ] **Step 3: Write minimal implementation**

`src/meeting/cli.py`:

```python
"""CLI entry point. Semua perintah tipis — logika di modul domain."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config, devices, indexmd, paths, queuer, recorder, recover, session

SUPPORTED_AUDIO = {".mp4", ".mp3", ".m4a", ".flac", ".wav", ".webm", ".ogg"}


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def cmd_record(args) -> int:
    paths.ensure_dirs()  # tmp/ dkk harus ada sebelum check_disk (statvfs butuh path)
    cfg = config.load()
    source = args.sys and "sys" or args.mic and "mic" or args.both and "both" or cfg["default_source"]
    if args.sys or args.mic or args.both:
        config.update(default_source=source)

    # pemulihan sesi yatim dulu (bila ada) — jangan tumpang tindih
    recover.offer_recovery(ui_confirm=_confirm)

    job_id = queuer.new_job_id(args.name)
    if not queuer.is_valid_id(job_id):
        _eprint(f"nama tidak valid: {job_id!r} (hanya huruf/angka/-/_)")
        return 1

    est_hours = 3.0
    try:
        recorder.check_disk(paths.tmp_dir(), est_hours=est_hours,
                            streams=2 if source == "both" else 1)
    except recorder.RecorderError as e:
        _eprint(str(e))
        return 1

    cfg = config.load()
    try:
        session.start({"source": source, "id": job_id, "wav_paths": [], "device": cfg.get("mic_device") or "default"})
    except session.SessionActiveError as e:
        _eprint(str(e))
        return 1

    print(f"Merekam ({source}) … Ctrl+C untuk stop. Job: {job_id}")
    try:
        meta = recorder.record(source, job_id, mic_device=cfg.get("mic_device"))
    except KeyboardInterrupt:
        # Ctrl+C: recorder berhenti karena stop-flag (di-set handler di bawah);
        # bila interrupt menghentikan record() sebelum return, baca WAV dari disk —
        # incremental writer menjamin file valid apa adanya.
        session.request_stop()
        meta = _meta_from_disk(job_id, source)

    if not meta["wav_paths"]:
        _eprint("tidak ada audio terekam — sesi dibatalkan")
        session.finish()
        return 1

    wav_rel = [str(w.relative_to(paths.home() / "data")) for w in meta["wav_paths"]]
    meta_out = session.finish()
    durasi = meta["duration_sec"]
    job = {
        "id": job_id,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,
        "audio": f"recordings/{job_id}.flac",  # target; worker isi setelah finalisasi
        "wav": wav_rel[0] if len(wav_rel) == 1 else wav_rel,
        "original_path": None,
        "needs_finalization": True,
        "duration_sec": durasi,
        "device": meta["device"],
        "model": cfg["model"],
        "status": "pending",
    }
    queuer.enqueue(job)
    indexmd.upsert(
        {
            "id": job_id,
            "tanggal": job["created_at"].replace("T", " "),
            "durasi": indexmd.fmt_duration(durasi),
            "status": "pending",
            "path": f"data/output/{job_id}/transcript.md",
        }
    )
    queuer.spawn_worker()
    print(f"Selesai merekam ({indexmd.fmt_duration(durasi)}). Job antrean: {job_id}")
    print(f"Terminal bebas — transkripsi jalan di background. Lihat: meeting status")
    return 0


def _confirm(msg: str) -> bool:
    try:
        ans = input(f"{msg} ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes", "ya")


def _meta_from_disk(job_id: str, source: str) -> dict:
    """Setelah Ctrl+C: bangun meta dari WAV incremental yang ada di tmp/."""
    import soundfile as sf

    suffixes = {"mic": ["_mic"], "sys": ["_sys"], "both": ["_mic", "_sys"]}.get(source, ["_mic", "_sys"])
    wavs = []
    for suf in suffixes:
        p = paths.tmp_dir() / f"{job_id}{suf}.wav"
        if p.exists():
            wavs.append(p)
    dur = 0.0
    for w in wavs:
        try:
            dur = max(dur, sf.info(w).frames / 16000)
        except Exception:
            pass
    return {"duration_sec": dur, "wav_paths": wavs, "device": "unknown", "device_lost": False}


def cmd_transcribe(args) -> int:
    src = Path(args.file)
    if not src.exists():
        _eprint(f"file tidak ditemukan: {src}")
        return 1
    ext = src.suffix.lower()
    cfg = config.load()
    job_id = queuer.new_job_id(Path(src).stem.replace(" ", "_")[:40] or None)
    audio_rel: str
    needs_fin = False

    if ext in SUPPORTED_AUDIO:
        audio_rel = str(src.resolve())  # absolut; file asli tak pernah diubah
    else:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            _eprint("Format ini butuh ffmpeg — install dengan: winget install ffmpeg")
            return 1
        paths.ensure_dirs()
        out = paths.tmp_dir() / f"{job_id}{ext}.flac"
        rc = subprocess.run(
            [ffmpeg, "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(out)],
            capture_output=True,
        )
        if rc.returncode != 0 or not out.exists():
            _eprint(f"ffmpeg gagal mengonversi {src.name}")
            return 1
        audio_rel = str(out.resolve())

    job = {
        "id": job_id,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "file",
        "audio": audio_rel,
        "wav": None,
        "original_path": str(src.resolve()),
        "needs_finalization": needs_fin,
        "duration_sec": None,
        "device": None,
        "model": cfg["model"],
        "status": "pending",
    }
    queuer.enqueue(job)
    indexmd.upsert(
        {
            "id": job_id,
            "tanggal": job["created_at"].replace("T", " "),
            "durasi": "-",
            "status": "pending",
            "path": f"data/output/{job_id}/transcript.md",
        }
    )
    queuer.spawn_worker()
    print(f"Job dibuat: {job_id} — meeting status untuk progres")
    return 0


def cmd_status(args) -> int:
    paths.ensure_dirs()
    pending = len(list(paths.queue_dir("pending").glob("*.json"))) + len(
        list(paths.queue_dir("pending").glob("*.processing"))
    )
    done = len(list(paths.queue_dir("done").glob("*.json")))
    failed = len(list(paths.queue_dir("failed").glob("*.json")))
    print(f"antrian — pending: {pending} | done: {done} | failed: {failed}")

    act = session.active()
    if act:
        print(f"sesi rekam aktif: id {act.get('id')} (mulai {act.get('started_at')})")

    le = paths.home() / "data" / "worker.last_error"
    if le.exists():
        print(f"worker error terakhir: {le.read_text(encoding='utf-8').strip()}")
    return 0


def cmd_list(args) -> int:
    rows = indexmd.rows()
    if not rows:
        print("belum ada meeting — INDEX.md kosong")
        return 0
    print(f"{'tanggal':<18}{'durasi':>8}  {'status':<8} id")
    for r in rows:
        print(f"{r['tanggal']:<18}{r['durasi']:>8}  {r['status']:<8} {r['id']}")
    return 0


def cmd_retry(args) -> int:
    paths.ensure_dirs()
    moved = 0
    for f in paths.queue_dir("failed").glob("*.json"):
        job = json.loads(f.read_text(encoding="utf-8"))
        job["status"] = "pending"
        job.pop("error", None)
        job.pop("finished_at", None)
        (paths.queue_dir("pending") / f.name).write_text(
            json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        f.unlink()
        moved += 1
    if moved:
        queuer.spawn_worker()
    print(f"{moved} job dikembalikan ke antrian")
    return 0


def cmd_devices(args) -> int:
    if args.mic is not None and args.mic != "none":
        devices.set_mic(args.mic)
        print(f"mic disetel: {args.mic}")
        return 0
    if args.mic == "none":
        devices.set_mic(None)
        print("mic disetel: default Windows")
        return 0
    for d in devices.list_devices():
        tag = "loopback" if d["kind"] == "loopback" else "mic"
        print(f"[{tag}] {d['name']}")
    print("\nSet mic:   meeting devices --mic \"Nama Mic\"")
    print("Reset default: meeting devices --mic none")
    return 0


def cmd_button(args) -> int:
    from . import button  # lazy: tkinter hanya saat dipakai

    button.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="meeting", description="Rekam & transkripsi meeting")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("record", help="rekam meeting (default: mode terakhir)")
    pr.add_argument("--sys", action="store_true", help="audio sistem (loopback)")
    pr.add_argument("--mic", action="store_true", help="mikrofon")
    pr.add_argument("--both", action="store_true", help="mic + audio sistem")
    pr.add_argument("--name", default=None, help="nama job (default: tanggal-jam)")
    pr.set_defaults(func=cmd_record)

    pt = sub.add_parser("transcribe", help="transkrip file yang sudah ada")
    pt.add_argument("file")
    pt.set_defaults(func=cmd_transcribe)

    ps = sub.add_parser("status", help="status antrian + sesi")
    ps.set_defaults(func=cmd_status)

    pl = sub.add_parser("list", help="daftar meeting dari INDEX.md")
    pl.set_defaults(func=cmd_list)

    prr = sub.add_parser("retry", help="kembalikan job failed ke antrian")
    prr.set_defaults(func=cmd_retry)

    pd = sub.add_parser("devices", help="listing / pilih device")
    pd.add_argument("--mic", default=None, help="nama mic (atau 'none' utk reset)")
    pd.set_defaults(func=cmd_devices)

    pb = sub.add_parser("button", help="floating record button")
    pb.set_defaults(func=cmd_button)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _eprint("\nDibatalkan.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
```

Catatan implementasi (informasional — kode di atas sudah memenuhi):
- Job `wav` untuk `--both` berupa **list** path; Task 7 (`worker.process`) sudah menormalkan `job["wav"]` string-atau-list menjadi list `Path` sebelum memanggil `finalizer.finalize`.
- `cmd_record` menangkap `KeyboardInterrupt` dengan `session.request_stop()` + `_meta_from_disk()`; edge Ctrl+C sesungguhnya diverifikasi integration test Task 14.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: 11 PASS (2 devices + 9 CLI)

- [ ] **Step 5: Commit**

```bash
git add src/meeting/cli.py tests/test_cli.py README.md
git commit -m "feat: CLI — record/transcribe/status/list/retry/devices with queue handoff"
```

---

## Task 13: Floating record button (tkinter)

**Files:**
- Create: `src/meeting/button.py`
- Test: `tests/test_button_logic.py` (logika state tanpa GUI — tkinter tidak jalan headless WSL)

**Interfaces:**
- Consumes: `session`, `recorder`, `queuer`, `config`, `paths`
- Produces: `button.run() -> None` (mainloop tkinter); `button.ButtonState` — state machine murni yang diuji: `idle → recording → saving → idle`. Logika GUI di `button.py` memakai `ButtonState` + timer via `widget.after(500, tick)`.

State machine (`ButtonState`, tanpa tkinter — bisa diuji):
- `start(source, job_id)` → recording; simpan `t0`.
- `stop()` → mengembalikan `elapsed_sec`.
- `phase` property: `"idle" | "recording" | "saving"`.
- Anti-dobel sudah di layer `session` (start raise `SessionActiveError` → button tampilkan pesan via `messagebox.showwarning` dan tetap idle).

GUI spec (implementasi di `button.py`, diverifikasi manual — checklist Task 15):
- Jendela `tk.Tk`, `overrideredirect(True)`, `attributes("-topmost", True)`, ukuran 56×56, background gelap `#1e1e1e`.
- Kanvas lingkaran: abu `#888` (idle) / merah `#e53935` berdenyut (mengubah radius sinusoidal tiap tick 120 ms) + label timer `M:SS`.
- Drag: bind `<Button-1>`/`<B1-Motion>` offset; posisi disimpan ke `config.update(button_x=…, button_y=…)` saat window close; dimuat saat start.
- Klik saat idle: `recover.offer_recovery(ui_confirm=messagebox.askyesno)` bila yatim; lalu `session.start` + `threading.Thread(target=recorder.record, daemon=True)`; klik saat recording: `session.request_stop()`; thread recorder berhenti → thread join → finalize di worker via enqueue (sama seperti CLI: bangun job, enqueue, spawn).
- Setelah stop: kilatan "✓ tersimpan" 1.5 s (warna hijau `#43a047`), lalu idle.
- `meeting button` auto-start login: shortcut `shell:startup` (README Task 12 sudah menyebut).

- [ ] **Step 1: Write the failing test**

`tests/test_button_logic.py`:

```python
import pytest

from meeting import button


def test_initial_idle():
    st = button.ButtonState()
    assert st.phase == "idle"


def test_start_then_recording():
    st = button.ButtonState()
    st.start(source="mic", job_id="j1")
    assert st.phase == "recording"


def test_stop_returns_elapsed_and_idle(monkeypatch):
    st = button.ButtonState()
    st.start(source="mic", job_id="j1")
    monkeypatch.setattr(button.time, "monotonic", lambda: st.t0 + 65.0)
    assert st.stop() == pytest.approx(65.0)
    assert st.phase == "idle"


def test_reuse_after_stop(monkeypatch):
    st = button.ButtonState()
    st.start(source="mic", job_id="a")
    monkeypatch.setattr(button.time, "monotonic", lambda: st.t0 + 5)
    st.stop()
    st.start(source="sys", job_id="b")
    assert st.phase == "recording"
    monkeypatch.setattr(button.time, "monotonic", lambda: st.t0 + 2)
    assert st.stop() == pytest.approx(2.0)


def test_source_and_job_id_roundtrip():
    st = button.ButtonState()
    st.start(source="both", job_id="xyz")
    assert st.source == "both"
    assert st.job_id == "xyz"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_button_logic.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write minimal implementation**

`src/meeting/button.py`:

```python
"""Floating record button — frontend GUI di atas core yang sama (session lock,
antrian, worker). Logika state di ButtonState (diuji unit); tkinter hanya shell."""
from __future__ import annotations

import threading
import time

try:  # tkinter opsional saat headless (WSL test) — GUI hanya butuh display
    import tkinter as tk
    from tkinter import messagebox
except ImportError:  # pragma: no cover
    tk = None
    messagebox = None

from . import config, indexmd, paths, queuer, recorder, recover, session

SIZE = 56
BG = "#1e1e1e"
IDLE_COLOR = "#888888"
REC_COLOR = "#e53935"
OK_COLOR = "#43a047"
TICK_MS = 120


class ButtonState:
    """State machine murni: idle → recording → (stop) → idle."""

    def __init__(self) -> None:
        self.phase = "idle"
        self.t0 = 0.0
        self.source: str | None = None
        self.job_id: str | None = None

    def start(self, source: str, job_id: str) -> None:
        self.phase = "recording"
        self.source = source
        self.job_id = job_id
        self.t0 = time.monotonic()

    @property
    def elapsed_sec(self) -> float:
        return time.monotonic() - self.t0 if self.phase == "recording" else 0.0

    def stop(self) -> float:
        el = self.elapsed_sec
        self.phase = "idle"
        return el


def _fmt(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


class FloatingButton:
    def __init__(self) -> None:
        if tk is None:
            raise RuntimeError("tkinter tidak tersedia — install python3-tk / Python dengan Tcl/Tk")
        self.state = ButtonState()
        self.rec_thread: threading.Thread | None = None
        self.rec_meta: dict | None = None

        self.root = tk.Tk()
        self.root.title("meeting")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"{SIZE}x{SIZE}")

        cfg = config.load()
        x, y = cfg.get("button_x"), cfg.get("button_y")
        if isinstance(x, int) and isinstance(y, int):
            self.root.geometry(f"+{x}+{y}")

        self.root.configure(bg=BG)
        self.canvas = tk.Canvas(self.root, width=SIZE, height=SIZE, bg=BG,
                                highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_click)
        self._drag_offset = (0, 0)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._pulse_t = 0.0
        self._tick()

    # ---------- interaksi ----------

    def _on_press(self, ev):
        self._drag_offset = (ev.x - SIZE // 2, ev.y - SIZE // 2)

    def _on_drag(self, ev):
        dx, dy = self._drag_offset
        x = self.root.winfo_pointerx() - dx
        y = self.root.winfo_pointery() - dy
        self.root.geometry(f"+{x}+{y}")

    def _on_close(self):
        # simpan posisi
        config.update(button_x=self.root.winfo_x(), button_y=self.root.winfo_y())
        if self.state.phase == "recording":
            session.request_stop()  # recorder proses lain menulis WAV tetap
        self.root.destroy()

    def _on_click(self, ev):
        if self.state.phase == "idle":
            self._start_recording()
        elif self.state.phase == "recording":
            self._stop_recording()

    def _start_recording(self):
        recover.offer_recovery(
            ui_confirm=lambda msg: messagebox.askyesno("Rekaman yatim", msg)
        )
        cfg = config.load()
        source = cfg.get("default_source", "mic")
        job_id = queuer.new_job_id()
        try:
            session.start({
                "source": source, "id": job_id, "wav_paths": [],
                "device": cfg.get("mic_device") or "default",
            })
        except session.SessionActiveError as e:
            messagebox.showwarning("Sesi aktif", str(e))
            return
        self.state.start(source, job_id)
        self.rec_meta = None
        self.rec_thread = threading.Thread(
            target=self._record_worker, args=(source, job_id, cfg), daemon=True
        )
        self.rec_thread.start()

    def _record_worker(self, source, job_id, cfg):
        self.rec_meta = recorder.record(source, job_id, mic_device=cfg.get("mic_device"))
        self.root.after(0, self._after_record_stopped)

    def _stop_recording(self):
        session.request_stop()

    def _after_record_stopped(self):
        meta = self.rec_meta or {"duration_sec": 0, "wav_paths": [], "device": "?"}
        if not meta.get("wav_paths"):
            session.finish()
            self.state.stop()
            return
        wav_rel = [str(w.relative_to(paths.home() / "data")) for w in meta["wav_paths"]]
        session_meta = session.finish()
        job = {
            "id": self.state.job_id,
            "created_at": session_meta.get("started_at"),
            "source": self.state.source,
            "audio": f"recordings/{self.state.job_id}.flac",
            "wav": wav_rel[0] if len(wav_rel) == 1 else wav_rel,
            "original_path": None,
            "needs_finalization": True,
            "duration_sec": meta["duration_sec"],
            "device": meta["device"],
            "model": config.load()["model"],
            "status": "pending",
        }
        queuer.enqueue(job)
        indexmd.upsert({
            "id": job["id"],
            "tanggal": (job["created_at"] or "").replace("T", " "),
            "durasi": indexmd.fmt_duration(job["duration_sec"]),
            "status": "pending",
            "path": f"data/output/{job['id']}/transcript.md",
        })
        queuer.spawn_worker()
        self.state.stop()
        self._flash_saved()

    # ---------- render ----------

    def _flash_saved(self):
        self._saved_until = time.monotonic() + 1.5

    def _tick(self):
        self._pulse_t += 0.12
        c = self.canvas
        c.delete("all")
        w, h = SIZE, SIZE
        if self.state.phase == "recording":
            r = 14 + 4 * (0.5 + 0.5 * ((self._pulse_t * 2) % 2 - 1))
            c.create_oval(w/2 - r, h/2 - r, w/2 + r, h/2 + r, fill=REC_COLOR, outline="")
            c.create_text(w/2, h - 10, text=_fmt(self.state.elapsed_sec),
                          fill="white", font=("Segoe UI", 8))
        elif getattr(self, "_saved_until", 0) > time.monotonic():
            r = 16
            c.create_oval(w/2 - r, h/2 - r, w/2 + r, h/2 + r, fill=OK_COLOR, outline="")
            c.create_text(w/2, h/2, text="✓", fill="white", font=("Segoe UI", 12, "bold"))
        else:
            r = 12
            c.create_oval(w/2 - r, h/2 - r, w/2 + r, h/2 + r, fill=IDLE_COLOR, outline="")
        self.root.after(TICK_MS, self._tick)

    def run(self):
        self.root.mainloop()


def run() -> None:
    FloatingButton().run()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_button_logic.py -v`
Expected: 5 PASS (tkinter tidak dibutuhkan — `FloatingButton` tidak diinstansiasi di test; import modul aman di WSL karena `tkinter` ada di stdlib, hanya `Tk()` yang butuh display)

- [ ] **Step 5: Commit**

```bash
git add src/meeting/button.py tests/test_button_logic.py
git commit -m "feat: floating always-on-top record button with drag, pulse, saved-flash"
```

---

## Task 14: Integration test opt-in (Windows, API key nyata)

**Files:**
- Create: `tests/test_integration_win.py`

**Interfaces:**
- Consumes: seluruh sistem via `cli.main`
- Produces: test yang **dijalankan manual di Windows** (`GEMINI_API_KEY` valid + mic ada), bukan di CI/WSL. Marker `@pytest.mark.integration`.

File `pyproject.toml` sudah mendaftar marker? Belum — tambahkan di `[tool.pytest.ini_options]`: `markers = ["integration: butuh hardware Windows + API key (jalankan manual)"]`, dan `addopts = "-m 'not integration'"` agar default skip.

- [ ] **Step 1: Write the test (skipped by default)**

`tests/test_integration_win.py`:

```python
import json
import time

import pytest

pytestmark = pytest.mark.integration


def _wait_job_done(meeting_home, timeout=300):
    from meeting import paths

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        done = list(paths.queue_dir("done").glob("*.json"))
        failed = list(paths.queue_dir("failed").glob("*.json"))
        if done or failed:
            return done, failed
        time.sleep(2)
    raise TimeoutError("job tidak selesai dalam 5 menit")


def test_record_10s_and_transcribe(meeting_home, monkeypatch):
    """Rekam 10 detik nyata → stop → worker → transcript.md berisi teks.

    Jalankan di Windows dengan mikrofon & .env berisi GEMINI_API_KEY valid:
        python -m pytest tests/test_integration_win.py -m integration -v
    """
    import sounddevice as sd

    from meeting import cli, paths

    paths.ensure_dirs()
    paths.env_file().parent.mkdir(parents=True, exist_ok=True)

    real_record = __import__("meeting.recorder", fromlist=["record"]).record

    def record_10s(source, job_id, mic_device=None):
        session_mod = __import__("meeting.session", fromlist=["request_stop"])
        import threading

        threading.Timer(10.0, session_mod.request_stop).start()
        return real_record(source, job_id, mic_device=mic_device)

    monkeypatch.setattr(cli.recorder, "record", record_10s)

    rc = cli.main(["record", "--name", "integ10"])
    assert rc == 0

    done, failed = _wait_job_done(meeting_home)
    assert not failed, f"job failed: {[f.name for f in failed]}"
    job = json.loads(done[0].read_text(encoding="utf-8"))
    outdir = paths.output_dir() / job["id"]
    md = (outdir / "transcript.md").read_text(encoding="utf-8")
    assert md.strip(), "transkrip kosong"
    meta = json.loads((outdir / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "done"
    assert meta["duration_sec"] >= 9.0
```

- [ ] **Step 2: Update pyproject pytest config**

Di `[tool.pytest.ini_options]` pyproject.toml, jadikan:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not integration'"
markers = ["integration: butuh hardware Windows + API key (jalankan manual)"]
```

- [ ] **Step 3: Run di WSL — verify integration skip, unit tetap hijau**

Run: `python -m pytest -v`
Expected: semua unit PASS, `test_integration_win.py` deselected/skipped

- [ ] **Step 4: (Windows, manual) jalankan integration**

Di PowerShell pada mesin target dengan mic + API key:

```powershell
python -m pytest tests/test_integration_win.py -m integration -v
```

Expected: 1 PASS dalam ~2–4 menit (10 s rekam + upload + transkripsi)

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration_win.py pyproject.toml
git commit -m "test: opt-in Windows integration test (10s record → transcript)"
```

---

## Task 15: Manual release checklist + final polish

**Files:**
- Create: `docs/MANUAL-CHECKLIST.md`

**Interfaces:**
- Consumes: seluruh sistem
- Produces: dokumen checklist rilis (dari spec) — dijalankan manusia di mesin Windows target.

- [ ] **Step 1: Write the checklist**

`docs/MANUAL-CHECKLIST.md`:

```markdown
# Checklist Manual per Rilis

Jalankan di mesin Windows target (mic/headset/speaker tersambung, .env berisi
GEMINI_API_KEY valid). Centang setiap item.

## Rekam

- [ ] `meeting record --mic` — berhenti dengan Ctrl+C; job muncul di `meeting status`;
      transcript.md muncul di `data/output/<id>/` dalam beberapa menit
- [ ] `meeting record --sys` — putar audio dari Zoom/Meet/YouTube; transkrip berisi
      ucapan dari audio sistem
- [ ] `meeting record --both` — FLAC final stereo (mic kiri, sistem kanan) — cek
      `data/recordings/<id>.flac` 2 kanal; transkrip tetap masuk akal
- [ ] `meeting record` (tanpa flag) — memakai mode terakhir (cek `config.json`
      `default_source` berubah setelah memakai flag)
- [ ] `meeting record --name "standup"` — folder output memakai nama

## Anti-dobel & tombol

- [ ] Tombol floating merekam → `meeting record` ditolak dengan pesan sesi aktif
- [ ] `meeting record` merekam → klik tombol ditolak (warning box)
- [ ] Klik tombol saat merekam → berhenti, ✓ tersimpan, job masuk antrian
- [ ] Posisi tombol setelah drag + tutup → diingat saat dibuka lagi
- [ ] Matikan widget (kill python) saat merekam → WAV tetap ditulis; klik record
      berikutnya menawarkan pemulihan yatim

## Ketahanan

- [ ] Crash-kill (taskkill /F) di tengah sesi → `meeting record` berikutnya
      menawarkan finalisasi rekaman yatim
- [ ] Tolak recovery → WAV pindah ke `data/tmp/discarded/` (tidak dihapus)
- [ ] Cabut headset saat merekam → rekaman berhenti rapi, job tetap dibuat
- [ ] Disk hampir penuh → pesan jelas sebelum mulai merekam

## Transkripsi file

- [ ] `meeting transcribe Zoom.mp4` → job langsung (tanpa ffmpeg)
- [ ] File format eksotis tanpa ffmpeg → pesan install satu kalimat
- [ ] (Opsional, ffmpeg terpasang) format eksotis dikonversi otomatis

## Worker

- [ ] Dua job cepat berturut → diproses FIFO, satu worker (cek `data/worker.log`)
- [ ] Putuskan jaringan saat job → job ke `failed/`; `meeting retry` setelah
      jaringan kembali → selesai
- [ ] API key salah → `meeting status` menampilkan penyebab
- [ ] File Gemini terhapus setelah sukses (cek di AI Studio "File Manager" bila mau)

## Back-to-back

- [ ] Stop rekaman → terminal bisa langsung dipakai `meeting record` lagi (<10 s)
- [ ] Dua meeting berturut → dua job di antrian diproses berurutan
```

- [ ] **Step 2: Verify unit suite masih hijau**

Run: `python -m pytest -v`
Expected: semua PASS, integration deselected

- [ ] **Step 3: Commit**

```bash
git add docs/MANUAL-CHECKLIST.md
git commit -m "docs: manual release checklist from design spec"
```

---

## Verification (setelah semua task)

```bash
python -m pytest -v          # WSL: semua unit test hijau
```

Lalu di Windows (manual): ikuti `docs/MANUAL-CHECKLIST.md`.

## Self-Review Checklist (untuk eksekutor plan — jalankan sekali di akhir)

1. **Spec coverage** — tiap baris "Perintah CLI" ada subcommand; tiap edge case di
   spec punya test atau item checklist manual.
2. **Placeholder scan** — tidak ada "TODO"/"implement later" tersisa di kode.
3. **Type consistency** — `worker.transcribe_audio` di-import by-name (monkeypatch);
   `job["wav"]` string-atau-list dinormalkan di worker; `finalizer.finalize` selalu
   menerima list Path.
```


README minimal di task ini (dipakai verifikasi manual):

```markdown
# Meeting Transcriber

Rekam meeting di Windows dan transkripsi otomatis via Gemini — semua hasil lokal.

## Setup

    pip install -e .
    copy .env.example .env    # isi GEMINI_API_KEY (Google AI Studio)

## Pakai

    meeting record            # mode terakhir (default mic)
    meeting record --sys      # audio sistem (Zoom/Meet dari speaker)
    meeting record --both     # mic + sistem
    meeting transcribe file.mp4
    meeting status            # antrian + sesi aktif
    meeting list              # INDEX.md
    meeting retry             # failed → antrian
    meeting devices           # listing / --mic "Nama"

Hasil: `data/output/<id>/transcript.md` + `INDEX.md`. Audio final di `data/recordings/` — tidak pernah dihapus otomatis.

## Floating record button

    meeting button

Auto-start saat login (opsional): buat shortcut `meeting button` di
`shell:startup` (Win+R → `shell:startup`).

## Troubleshooting

- `ffmpeg` dibutuhkan hanya untuk format eksotis: `winget install ffmpeg`
- Worker log: `data/worker.log`; error terakhir: `meeting status`
```
