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
