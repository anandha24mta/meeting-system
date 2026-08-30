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


def reclaim_processing() -> int:
    """Kembalikan sisa *.json.processing ke *.json.

    Worker yang mati di tengah job (PC shutdown saat backoff 429) meninggalkan
    file .processing yang tak pernah diproses lagi & terhitung pending selamanya.
    Aman dipanggil hanya saat lock dipegang — pemegang lock sebelumnya pasti
    sudah mati (lock dilepas via unlink saat exit normal; stale diambil alih)."""
    n = 0
    for f in paths.queue_dir("pending").glob("*.processing"):
        os.replace(f, f.with_name(f.name[: -len(".processing")]))
        _log("info", f"reclaim {f.name} → pending (sisa worker mati)")
        n += 1
    return n


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
        time.sleep(1.0)  # jangan tight-loop: error persisten ≠ spin CPU/log tanpa batas
    return True


def main() -> int:
    paths.ensure_dirs()
    if not _acquire_lock():
        _log("info", "worker lain sudah aktif — exit")
        return 0
    try:
        reclaim_processing()  # sisa worker mati → pending lagi (lock = pemilik lama pasti mati)
        while run_once():
            pass
        # sukses total: hapus error lama agar `meeting status` tak melaporkan
        # "API key invalid" selamanya setelah key diperbaiki
        (paths.home() / "data" / "worker.last_error").unlink(missing_ok=True)
    except FatalWorkerError as e:
        _log("error", f"worker exit fatal: {e}")  # last_error sudah ditulis process()
        return 1
    finally:
        _release_lock()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
