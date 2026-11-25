import json
import shutil
import subprocess
import time
from pathlib import Path

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


def _seed_api_key(meeting_home):
    import os

    from meeting import paths

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        repo_env = Path(__file__).resolve().parent.parent / ".env"
        if repo_env.exists():
            for line in repo_env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("GEMINI_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    assert key, "GEMINI_API_KEY tidak ditemukan — set env var atau isi .env"
    paths.env_file().write_text(f"GEMINI_API_KEY={key}\n", encoding="utf-8")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="butuh ffmpeg")
def test_record_m4a_accepted_by_gemini(meeting_home, monkeypatch):
    """Manual Windows: rekam 10 detik dgn audio_codec=aac → m4a diputar & ditranskrip."""
    import threading

    from meeting import cli, config, paths, recorder, session

    _seed_api_key(meeting_home)
    paths.ensure_dirs()
    config.update(audio_codec="aac", container="m4a")
    real_record = recorder.record

    def record_10s(source, job_id, mic_device=None):
        threading.Timer(10.0, session.request_stop).start()
        return real_record(source, job_id, mic_device=mic_device)

    monkeypatch.setattr(cli.recorder, "record", record_10s)
    assert cli.main(["record", "--name", "integ-m4a"]) == 0

    done, failed = _wait_job_done(meeting_home)
    assert not failed, f"job failed: {[f.name for f in failed]}"
    job = json.loads(done[0].read_text(encoding="utf-8"))
    assert job["audio"].endswith(".m4a")
    assert Path(job["audio"]).exists()
    outdir = paths.output_dir() / job["id"]
    meta = json.loads((outdir / "meta.json").read_text(encoding="utf-8"))
    assert meta["codec"] == "aac"
    assert meta["container"] == "m4a"
    assert meta["vbr"] is True
    assert (outdir / "transcript.txt").read_text(encoding="utf-8").strip()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="butuh ffmpeg")
def test_mp4_video_passthrough_byte_identical(meeting_home, tmp_path, monkeypatch):
    """Manual Windows: video mp4 ikut ter-remux byte-identical, VFR utuh."""
    from meeting import cli, config, paths

    _seed_api_key(meeting_home)
    paths.ensure_dirs()
    config.update(audio_codec="aac", container="auto")
    monkeypatch.setattr(cli.queuer, "spawn_worker", lambda: None)
    ffmpeg = shutil.which("ffmpeg")
    src = tmp_path / "vfr.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src)],
        capture_output=True, check=True,
    )

    def video_md5(path):
        out = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy",
             "-f", "md5", "-"], capture_output=True, text=True,
        )
        return out.stdout.strip()

    assert cli.main(["transcribe", str(src)]) == 0
    job_file = list(paths.queue_dir("pending").glob("*.json"))[0]
    job = json.loads(job_file.read_text(encoding="utf-8"))
    assert job["video_passthrough"] is True and job["vfr"] is True
    assert video_md5(src) == video_md5(job["audio"])


def test_ffmpeg_absent_fallback_keeps_source(meeting_home, tmp_path, monkeypatch):
    """Manual Windows: tanpa ffmpeg di PATH, input mp4 tetap diproses sbg source."""
    from meeting import cli, config, paths, queuer

    paths.ensure_dirs()
    config.update(audio_codec="aac")
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(queuer, "spawn_worker", lambda: None)
    src = tmp_path / "voice.mp4"
    src.write_bytes(b"fake")

    assert cli.main(["transcribe", str(src)]) == 0
    job = json.loads(list(paths.queue_dir("pending").glob("*.json"))[0].read_text())
    assert job["audio"] == str(src)
    assert "codec" not in job
