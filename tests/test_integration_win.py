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
