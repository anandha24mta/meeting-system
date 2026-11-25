from pathlib import Path

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


def _opus_cfg(meeting_home, **overrides):
    from meeting import config
    cfg = config.load()
    cfg.update(codec="opus", quality="balanced")
    cfg.update(overrides)
    return cfg


def _fake_opus_encode(monkeypatch, *, decode=True, duration=1.0, fail=None):
    calls = []

    def encode(src, out, *, bitrate_kbps, channels, samplerate=16000):
        calls.append({
            "src": Path(src), "out": Path(out), "bitrate": bitrate_kbps,
            "channels": channels, "samplerate": samplerate,
        })
        if fail is not None:
            raise fail
        Path(out).write_bytes(b"opus")
        return Path(out)

    monkeypatch.setattr(finalizer.codecs, "encode_opus", encode)
    monkeypatch.setattr(finalizer.codecs, "probe_duration", lambda _p: duration)
    monkeypatch.setattr(finalizer.codecs, "decode_ok", lambda _p: decode)
    return calls


def test_opus_profile_produces_opus_and_deletes_wav(meeting_home, wav_mono, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    calls = _fake_opus_encode(monkeypatch)
    out = finalizer.finalize([wav_mono], "opus-mono", _opus_cfg(meeting_home))
    assert out == paths.recordings_dir() / "opus-mono.opus"
    assert calls[0]["bitrate"] == 32
    assert calls[0]["channels"] == 1
    assert calls[0]["src"] == wav_mono  # langsung dari WAV, tanpa numpy penuh
    assert not wav_mono.exists()


def test_flac_stays_default_even_when_quality_set(meeting_home, wav_mono):
    from meeting import config, paths
    paths.ensure_dirs()
    cfg = config.load()
    cfg.update(quality="best")  # codec tidak diset → tetap FLAC
    out = finalizer.finalize([wav_mono], "still-flac", cfg)
    assert out.suffix == ".flac"
    assert out.exists()


def test_opus_verify_failure_preserves_wav(meeting_home, wav_mono, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    _fake_opus_encode(monkeypatch, decode=False)
    with pytest.raises(finalizer.FinalizeError, match="verify"):
        finalizer.finalize([wav_mono], "opus-bad", _opus_cfg(meeting_home))
    assert wav_mono.exists()
    assert not (paths.recordings_dir() / "opus-bad.opus").exists()


def test_opus_encode_error_preserves_wav(meeting_home, wav_mono, monkeypatch):
    from meeting import codecs, paths
    paths.ensure_dirs()
    _fake_opus_encode(monkeypatch, fail=codecs.CodecError("ffmpeg tidak ditemukan"))
    with pytest.raises(finalizer.FinalizeError, match="ffmpeg"):
        finalizer.finalize([wav_mono], "opus-fail", _opus_cfg(meeting_home))
    assert wav_mono.exists()


def test_opus_two_wavs_merged_to_stereo_stream(meeting_home, tmp_path, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    sr = 16000
    t = np.linspace(0, 0.5, sr // 2, endpoint=False)
    w1, w2 = tmp_path / "a.wav", tmp_path / "b.wav"
    sf.write(w1, (0.2 * np.sin(2 * np.pi * 300 * t)).astype(np.float32), sr, subtype="PCM_16")
    sf.write(w2, (0.2 * np.sin(2 * np.pi * 500 * t)).astype(np.float32), sr, subtype="PCM_16")

    calls, shapes = [], []

    def capture(src, out, *, bitrate_kbps, channels, samplerate=16000):
        data, _sr = sf.read(str(src), dtype="float32")
        shapes.append(data.shape)
        calls.append({"src": Path(src), "bitrate": bitrate_kbps, "channels": channels})
        Path(out).write_bytes(b"opus")
        return Path(out)

    monkeypatch.setattr(finalizer.codecs, "encode_opus", capture)
    monkeypatch.setattr(finalizer.codecs, "probe_duration", lambda _p: 0.5)
    monkeypatch.setattr(finalizer.codecs, "decode_ok", lambda _p: True)
    out = finalizer.finalize([w1, w2], "opus-stereo", _opus_cfg(meeting_home))
    assert out.suffix == ".opus"
    assert calls[0]["bitrate"] == 64 and calls[0]["channels"] == 2
    assert shapes == [(sr // 2, 2)]
    assert not (paths.tmp_dir() / "opus-stereo_merged.wav").exists()
    assert not w1.exists() and not w2.exists()
