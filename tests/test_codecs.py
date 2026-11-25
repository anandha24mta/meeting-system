import numpy as np
import pytest
import soundfile as sf

from meeting import codecs


def test_resolve_mime_by_extension():
    assert codecs.resolve_mime("a.wav") == "audio/wav"
    assert codecs.resolve_mime("a.flac") == "audio/flac"
    assert codecs.resolve_mime("a.opus") == "audio/ogg"
    assert codecs.resolve_mime("a.bin") == "application/octet-stream"


def test_probe_duration_uses_soundfile_for_wav(tmp_path):
    wav = tmp_path / "a.wav"
    sf.write(str(wav), np.zeros(1600, dtype=np.float32), 16000, subtype="PCM_16")
    assert codecs.probe_duration(wav) == pytest.approx(0.1)


def test_probe_duration_falls_back_to_ffprobe(tmp_path, monkeypatch):
    monkeypatch.setattr(codecs, "ffprobe_bin", lambda: "/usr/bin/ffprobe")

    def fake_run(argv, **kw):
        assert "format=duration" in argv
        return type("R", (), {"stdout": "12.5\n", "returncode": 0})()

    monkeypatch.setattr(codecs.subprocess, "run", fake_run)
    assert codecs.probe_duration(tmp_path / "a.opus") == pytest.approx(12.5)


def test_build_opus_argv_uses_direct_file_input_and_libopus():
    argv = codecs.build_opus_argv(
        "/usr/bin/ffmpeg", "in.wav", "out.opus",
        bitrate_kbps=32, channels=1,
    )
    assert argv[0] == "/usr/bin/ffmpeg"
    assert argv[argv.index("-i") + 1] == "in.wav"
    assert argv[argv.index("-c:a") + 1] == "libopus"
    assert argv[argv.index("-b:a") + 1] == "32k"
    assert argv[argv.index("-ac") + 1] == "1"
    assert argv[-1] == "out.opus"


def test_encode_opus_missing_ffmpeg_raises(monkeypatch):
    monkeypatch.setattr(codecs, "ffmpeg_bin", lambda: None)
    with pytest.raises(codecs.CodecError, match="ffmpeg"):
        codecs.encode_opus("in.wav", "out.opus", bitrate_kbps=32, channels=1)


def test_encode_opus_runs_ffmpeg_and_returns_output(tmp_path, monkeypatch):
    out = tmp_path / "out.opus"
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        out.write_bytes(b"opus")
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(codecs, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(codecs.subprocess, "run", fake_run)
    got = codecs.encode_opus("in.wav", out, bitrate_kbps=32, channels=1)
    assert got == out
    assert seen["argv"][0] == "/usr/bin/ffmpeg"


def test_encode_opus_nonzero_rc_raises(monkeypatch):
    monkeypatch.setattr(codecs, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        codecs.subprocess, "run",
        lambda *a, **kw: type("R", (), {"returncode": 1})(),
    )
    with pytest.raises(codecs.CodecError, match="gagal"):
        codecs.encode_opus("in.wav", "out.opus", bitrate_kbps=32, channels=1)


def test_opus_bitrate_per_quality_and_channels():
    assert codecs.opus_bitrate("best", 1) == 64
    assert codecs.opus_bitrate("best", 2) == 128
    assert codecs.opus_bitrate("balanced", 1) == 32
    assert codecs.opus_bitrate("balanced", 2) == 64
    assert codecs.opus_bitrate("compact", 1) == 24
    assert codecs.opus_bitrate("compact", 2) == 48
    assert codecs.opus_bitrate("archive", 2) == 64  # fallback balanced


def test_opus_argv_uses_vbr_voip_and_max_compression():
    argv = codecs.build_opus_argv(
        "/usr/bin/ffmpeg", "in.wav", "out.opus", bitrate_kbps=32, channels=2
    )
    assert argv[argv.index("-vbr") + 1] == "on"
    assert argv[argv.index("-application") + 1] == "voip"
    assert argv[argv.index("-compression_level") + 1] == "10"
    assert "-nostdin" in argv


def test_decode_ok_decodes_full_file_via_null_sink(monkeypatch):
    seen = {}
    monkeypatch.setattr(codecs, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(codecs.subprocess, "run", fake_run)
    assert codecs.decode_ok("out.opus") is True
    assert seen["argv"][seen["argv"].index("-f") + 1] == "null"


def test_decode_ok_false_on_failure_or_missing_ffmpeg(monkeypatch):
    monkeypatch.setattr(codecs, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        codecs.subprocess, "run",
        lambda *a, **kw: type("R", (), {"returncode": 1})(),
    )
    assert codecs.decode_ok("out.opus") is False
    monkeypatch.setattr(codecs, "ffmpeg_bin", lambda: None)
    assert codecs.decode_ok("out.opus") is False
