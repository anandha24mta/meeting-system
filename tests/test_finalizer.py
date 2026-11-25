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


def test_finalize_streams_instead_of_reading_whole_file(meeting_home, wav_mono, monkeypatch):
    from meeting import paths

    paths.ensure_dirs()

    def forbidden(*args, **kwargs):
        raise AssertionError("sf.read would load the whole meeting into RAM")

    monkeypatch.setattr(sf, "read", forbidden)
    out = finalizer.finalize([wav_mono], "streamed")
    assert sf.info(out).frames == 16000
