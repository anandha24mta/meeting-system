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

    Jalankan di Windows dengan mikrofon & GEMINI_API_KEY di env (atau .env root proyek):
        python -m pytest tests/test_integration_win.py -m integration -v
    """
    import os
    import threading
    from pathlib import Path

    from meeting import cli, paths, recorder, session

    paths.ensure_dirs()

    # MEETING_HOME diarahkan ke tmp — seed .env di home itu, jika tidak
    # worker tidak akan pernah menemukan API key (api_key() hanya baca .env home).
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        repo_env = Path(__file__).resolve().parent.parent / ".env"
        if repo_env.exists():
            for line in repo_env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("GEMINI_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    assert key, (
        "GEMINI_API_KEY tidak ditemukan — set env var atau isi .env di root proyek"
    )
    paths.env_file().write_text(f"GEMINI_API_KEY={key}\n", encoding="utf-8")

    real_record = recorder.record

    def record_10s(source, job_id, mic_device=None):
        threading.Timer(10.0, session.request_stop).start()
        return real_record(source, job_id, mic_device=mic_device)

    monkeypatch.setattr(cli.recorder, "record", record_10s)

    rc = cli.main(["record", "--name", "integ10"])
    assert rc == 0

    done, failed = _wait_job_done(meeting_home)
    assert not failed, f"job failed: {[f.name for f in failed]}"
    job = json.loads(done[0].read_text(encoding="utf-8"))
    outdir = paths.output_dir() / job["id"]
    md = (outdir / "transcript.md").read_text(encoding="utf-8")
    body = md.split("---", 1)[-1].strip()  # teks setelah header metadata
    assert body, "transkrip kosong"
    txt = (outdir / "transcript.txt").read_text(encoding="utf-8").strip()
    assert txt, "transcript.txt kosong"
    meta = json.loads((outdir / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "done"
    assert meta["duration_sec"] >= 9.0
