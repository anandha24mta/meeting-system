from pathlib import Path

import pytest

from meeting import media


def test_aac_output_argv_native_ffmpeg_vbr():
    argv = media.aac_output_argv("in.flac", "out.m4a", 1, encoders=set())
    assert argv[:3] == ["-y", "-i", "in.flac"]
    assert argv[argv.index("-c:a") + 1] == "aac"
    assert argv[argv.index("-profile:a") + 1] == "aac_low"
    assert argv[argv.index("-q:a") + 1] == "0.5"
    assert "-vbr" not in argv
    assert "-vn" in argv
    assert argv[-1] == "out.m4a"


def test_aac_output_argv_libfdk_vbr():
    argv = media.aac_output_argv("in.flac", "out.m4a", 2, encoders={"libfdk_aac"})
    assert argv[argv.index("-c:a") + 1] == "libfdk_aac"
    assert argv[argv.index("-vbr") + 1] == "4"
    assert "-q:a" not in argv
    assert argv[argv.index("-ac") + 1] == "2"


def test_aac_bitrate_targets():
    assert media.aac_bitrate(1) == 64000
    assert media.aac_bitrate(2) == 128000


def test_encode_aac_invokes_ffmpeg_with_aac_lc_argv(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"m4a")
        return type("R", (), {"returncode": 0, "stdout": ""})()

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    out = media.encode_aac("in.flac", tmp_path / "out.m4a", 1, ffmpeg="ffmpeg", encoders=set())
    assert out.exists()
    assert "-c:a" in calls[0] and calls[0][calls[0].index("-c:a") + 1] == "aac"
    assert calls[0].count("aac_low") == 1


def test_encode_aac_without_ffmpeg_gives_actionable_error(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    with pytest.raises(media.MediaError, match="winget install ffmpeg"):
        media.encode_aac("in.flac", "out.m4a")


def test_probe_encoders_reads_ffmpeg_encoder_list(monkeypatch):
    listing = " A..... aac              AAC (Advanced Audio Coding)\n A..... libfdk_aac      Fraunhofer FDK AAC\n V....D libx264         H.264\n"
    monkeypatch.setattr(media.subprocess, "run", lambda cmd, **kw: type("R", (), {"stdout": listing})())
    found = media.probe_encoders("ffmpeg")
    assert {"aac", "libfdk_aac", "libx264"} <= found


def test_probe_encoders_without_ffmpeg_is_empty(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    assert media.probe_encoders() == set()


def test_audio_mime_covers_m4a_and_video_containers():
    assert media.audio_mime("clip.m4a") == "audio/mp4"
    assert media.audio_mime("voice.mp4") == "audio/mp4"
    assert media.audio_mime("voice.mkv") == "audio/x-matroska"
    assert media.audio_mime("clip.WAV") == "audio/wav"


def test_container_mime_distinguishes_audio_only_from_video():
    assert media.container_mime("voice.m4a", False) == "audio/mp4"
    assert media.container_mime("zoom.mp4", False) == "audio/mp4"
    assert media.container_mime("zoom.mp4", True) is None
    assert media.container_mime("zoom.mkv", True) == "video/x-matroska"


def test_wants_aac_only_on_explicit_opt_in():
    assert media.wants_aac({}) is False
    assert media.wants_aac({"audio_codec": "flac"}) is False
    assert media.wants_aac({"audio_codec": "aac"}) is True


def test_container_for_auto_picks_m4a_for_recordings_and_source_for_files():
    assert media.container_for({}) == "m4a"
    assert media.container_for({"container": "auto"}, source_ext=".mp4") == "mp4"
    assert media.container_for({"container": "auto"}, source_ext=".mkv") == "mkv"
    assert media.container_for({"container": "auto"}, source_ext=".mp3") == "m4a"


def test_container_for_explicit_choice_and_bad_value():
    assert media.container_for({"container": "m4a"}, source_ext=".mp4") == "m4a"
    with pytest.raises(media.MediaError, match="container tidak dikenal"):
        media.container_for({"container": "avi"})


def test_passthrough_argv_copies_video_without_reencode():
    argv = media.passthrough_argv("in.mp4", "out.mp4", video=True, encoders=set())
    assert argv[:2] == ["-y", "-i"]
    assert argv[argv.index("-c:v") + 1] == "copy"
    assert "-vn" not in argv
    assert argv[argv.index("-c:a") + 1] == "aac"
    assert not any(f in argv for f in ("-crf", "-r", "-b:v", "-fps_mode"))
    assert argv[-1] == "out.mp4"


def test_passthrough_argv_extracts_audio_only_when_no_video():
    argv = media.passthrough_argv("in.mkv", "out.m4a", video=False, encoders=set())
    assert "-vn" in argv
    assert "-c:v" not in argv
    assert argv[argv.index("-c:a") + 1] == "aac"


def test_probe_video_reads_ffprobe_stream_list(monkeypatch):
    out = type("R", (), {"stdout": "video\n"})()
    assert media.probe_video("in.mp4", ffprobe="ffprobe", run=lambda *a, **k: out) is True
    out.stdout = ""
    assert media.probe_video("in.mp4", ffprobe="ffprobe", run=lambda *a, **k: out) is False


def test_probe_video_without_ffprobe_is_false(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    assert media.probe_video("in.mp4") is False


def test_crf_and_vfr_args():
    assert media.crf_args(20) == ["-crf", "20"]
    assert media.vfr_args() == ["-fps_mode", "vfr"]


def test_hwaccel_args_inactive_unless_video_reencode():
    def boom(*args, **kwargs):
        raise AssertionError("probe laundry saat audio / video copy")

    assert media.hwaccel_args(False, run=boom) == []
    assert media.video_encode_argv("in.mp4", "out.mp4", run=boom) == [
        "-y", "-i", "in.mp4", "-c:v", "copy", "out.mp4",
    ]
    assert "-hwaccel" not in media.aac_output_argv("in.flac", "out.m4a", encoders=set())
    assert "-hwaccel" not in media.passthrough_argv("in.mp4", "out.mp4", encoders=set())


def test_probe_hwaccel_prefers_known_accelerators():
    out = type("R", (), {"stdout": "hwaccels:\nqsv\nd3d11va\ncuda\n"})()
    assert media.probe_hwaccel("ffmpeg", run=lambda *a, **k: out) == "qsv"
    out.stdout = "hwaccels:\nnvenc\n"
    assert media.probe_hwaccel("ffmpeg", run=lambda *a, **k: out) == "nvenc"
    out.stdout = "hwaccels:\nd3d11va\n"
    assert media.probe_hwaccel("ffmpeg", run=lambda *a, **k: out) == "d3d11va"
    out.stdout = "hwaccels:\ncuda\n"
    assert media.probe_hwaccel("ffmpeg", run=lambda *a, **k: out) == "auto"
    out.stdout = "hwaccels:\n"
    assert media.probe_hwaccel("ffmpeg", run=lambda *a, **k: out) is None


def test_probe_hwaccel_without_ffmpeg_is_none(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    assert media.probe_hwaccel() is None


def test_video_encode_argv_reencode_orders_hwaccel_before_input():
    out = type("R", (), {"stdout": "hwaccels:\nqsv\n"})()
    argv = media.video_encode_argv(
        "in.mp4", "out.mp4", video_reencode=True, ffmpeg="ffmpeg",
        run=lambda *a, **k: out,
    )
    assert argv[:2] == ["-hwaccel", "qsv"]
    assert argv.index("-hwaccel") < argv.index("-i")
    assert argv[argv.index("-crf") + 1] == "23"
    assert argv[argv.index("-fps_mode") + 1] == "vfr"
    assert "-c:a" not in argv
    assert argv[-1] == "out.mp4"


def test_video_encode_argv_reencode_without_vfr_and_explicit_hwaccel():
    argv = media.video_encode_argv(
        "in.mp4", "out.mp4", video_reencode=True, vfr=False, hwaccel="nvenc",
        run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("probe tak perlu")),
    )
    assert "-fps_mode" not in argv
    assert argv[:2] == ["-hwaccel", "nvenc"]
