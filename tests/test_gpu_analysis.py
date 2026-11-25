import numpy as np
import pytest
import soundfile as sf

from meeting import config, paths, transcribe


@pytest.fixture
def audio(meeting_home):
    paths.ensure_dirs()
    p = paths.recordings_dir() / "gpu.flac"
    t = np.linspace(0, 0.5, 8000, endpoint=False)
    sf.write(p, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000, format="FLAC")
    return p


def test_cpu_is_default_analysis_device(meeting_home, audio):
    assert config.load()["analysis_device"] == "cpu"
    assert transcribe._rms(audio) == transcribe._cpu_rms(audio)


def test_gpu_analysis_opt_in_uses_gpu_path(meeting_home, audio, monkeypatch):
    config.update(analysis_device="gpu")
    monkeypatch.setattr(transcribe, "_probe_duration", lambda p: 900.0)
    monkeypatch.setattr(transcribe.hwaccel, "probe", lambda: {"available": True})
    calls = []
    monkeypatch.setattr(transcribe, "_gpu_rms", lambda p: calls.append(1) or 0.25)
    assert transcribe._rms(audio) == 0.25
    assert len(calls) == 1


def test_gpu_analysis_falls_back_to_cpu(meeting_home, audio, monkeypatch):
    config.update(analysis_device="gpu")
    monkeypatch.setattr(transcribe, "_probe_duration", lambda p: 900.0)
    monkeypatch.setattr(transcribe.hwaccel, "probe", lambda: {"available": True})

    def boom(path):
        raise ImportError("no cupy installed")

    monkeypatch.setattr(transcribe, "_gpu_rms", boom)
    expected = transcribe._cpu_rms(audio)
    assert transcribe._rms(audio) == expected


def test_gpu_analysis_skipped_for_short_audio(meeting_home, audio, monkeypatch):
    config.update(analysis_device="gpu")
    monkeypatch.setattr(transcribe, "_probe_duration", lambda p: 10.0)
    monkeypatch.setattr(transcribe.hwaccel, "probe", lambda: {"available": True})
    monkeypatch.setattr(transcribe, "_gpu_rms", lambda p: 1.0)
    assert transcribe._rms(audio) == transcribe._cpu_rms(audio)
