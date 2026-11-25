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


# ... lanjutan test_cli.py (setelah bagian devices Task 11)
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
    rc = cli.main(["record"])
    assert rc == 0
    job_files = list(paths.queue_dir("pending").glob("*.json"))
    assert len(job_files) == 1
    job = json.loads(job_files[0].read_text(encoding="utf-8"))
    assert job["source"] == "mic"          # default config
    assert job["needs_finalization"] is True
    assert config.load()["default_source"] == "mic"


def test_record_max_workers_updates_config(meeting_home, fake_record, monkeypatch):
    monkeypatch.setattr(cli.queuer, "spawn_worker", lambda: None)
    assert cli.main(["record", "--max-workers", "3"]) == 0
    assert config.load()["max_workers"] == 3


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
    expected_wav = paths.tmp_dir() / f"{job['id']}_mic.wav"
    assert job["wav"] == str(expected_wav.relative_to(paths.home() / "data"))
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


def test_transcribe_sanitizes_exotic_filename(meeting_home, tmp_path, monkeypatch):
    """Stem dgn titik/kurung (nama file Zoom asli) → id legal, bukan traceback."""
    monkeypatch.setattr(cli.queuer, "spawn_worker", lambda: None)
    src = tmp_path / "Zoom Meeting 2026-08-28 20.15.42.wav"
    src.write_bytes(b"\x00" * 16)
    rc = cli.main(["transcribe", str(src)])
    assert rc == 0
    job = json.loads(list(paths.queue_dir("pending").glob("*.json"))[0].read_text(encoding="utf-8"))
    from meeting import queuer as q
    assert q.is_valid_id(job["id"])
    assert "20_15_42" in job["id"]  # titik diganti _


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
