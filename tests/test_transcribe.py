import json
from pathlib import Path

import pytest

from meeting import transcribe


class FakeFile:
    def __init__(self, name="files/fake-1"):
        self.name = name
        self.state = "PROCESSING"


class FakeFiles:
    """files.upload/get/delete dengan state transisi PROCESSING -> ACTIVE."""

    def __init__(self, text="Halo, ini transkrip.", fail_generate=False):
        self.text = text
        self.fail_generate = fail_generate
        self.uploaded = []
        self.upload_configs = []
        self.deleted = []
        self.get_calls = 0

    def upload(self, *, file, config=None):
        self.uploaded.append(str(file))
        self.upload_configs.append(config)
        return FakeFile()

    def get(self, *, name):
        self.get_calls += 1
        f = FakeFile(name)
        f.state = "ACTIVE" if self.get_calls >= 2 else "PROCESSING"
        return f

    def delete(self, *, name):
        self.deleted.append(name)


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, files, fail_generate=False):
        self.files = files
        self.fail_generate = fail_generate
        self.calls = 0

    def generate_content(self, *, model, contents):
        self.calls += 1
        if self.fail_generate:
            r = FakeResponse(None)
            # emulasi SDK: response.text raise bila tidak ada parts
            raise ValueError("no text")
        return FakeResponse(self.files.text)


class FakeClient:
    def __init__(self, text="Halo, ini transkrip.", fail_generate=False):
        self.files = FakeFiles(text)
        self.models = FakeModels(self.files, fail_generate)


def make_job(source="mic", model="gemini-flash"):
    return {
        "id": "2026-08-29_1400-standup",
        "created_at": "2026-08-29T14:00:05",
        "source": source,
        "audio": "recordings/2026-08-29_1400-standup.flac",
        "wav": None,
        "original_path": None,
        "needs_finalization": False,
        "duration_sec": 3661.0,
        "device": "Headset (Jabra)",
        "model": model,
        "status": "pending",
    }


@pytest.fixture
def audio(meeting_home, tmp_path):
    import numpy as np
    import soundfile as sf
    t = np.linspace(0, 0.5, 8000, endpoint=False)
    data = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    p = meeting_home / "data" / "recordings" / "a.flac"
    p.parent.mkdir(parents=True, exist_ok=True)
    sf.write(p, data, 16000, format="FLAC")
    return p


def test_transcribe_writes_outputs(meeting_home, audio):
    from meeting import paths
    paths.ensure_dirs()
    job = make_job()
    client = FakeClient(text="Pembicara 1: Halo semuanya.")
    meta = transcribe.transcribe_audio(audio, job, client=client)

    outdir = paths.output_dir() / job["id"]
    md = (outdir / "transcript.md").read_text(encoding="utf-8")
    txt = (outdir / "transcript.txt").read_text(encoding="utf-8")
    meta_json = json.loads((outdir / "meta.json").read_text(encoding="utf-8"))

    assert "Pembicara 1: Halo semuanya." in md
    assert "Pembicara 1: Halo semuanya." in txt
    # metadata header di .md: tanggal, durasi, device, model
    assert "2026-08-29T14:00:05" in md
    assert "1:01:01" in md
    assert "Headset (Jabra)" in md
    assert "gemini-flash" in md
    assert meta_json["status"] == "done"
    assert meta["status"] == "done"


def test_transcribe_polls_until_active(meeting_home, audio):
    from meeting import paths
    paths.ensure_dirs()
    client = FakeClient()
    transcribe.transcribe_audio(audio, make_job(), client=client)
    assert client.files.get_calls >= 2  # poll minimal sekali setelah PROCESSING


def test_transcribe_uploads_then_deletes_remote_file(meeting_home, audio):
    from meeting import paths
    paths.ensure_dirs()
    client = FakeClient()
    transcribe.transcribe_audio(audio, make_job(), client=client)
    assert len(client.files.uploaded) == 1
    assert client.files.deleted == ["files/fake-1"]


def test_transcribe_empty_text_raises_and_writes_nothing(meeting_home, audio):
    from meeting import paths
    paths.ensure_dirs()
    job = make_job()
    client = FakeClient(fail_generate=True)
    with pytest.raises(transcribe.TranscribeError):
        transcribe.transcribe_audio(audio, job, client=client)
    outdir = paths.output_dir() / job["id"]
    assert not outdir.exists()


def test_transcribe_requires_api_key_without_client(meeting_home, audio, monkeypatch):
    from meeting import config as cfg
    monkeypatch.setattr(cfg, "api_key", lambda: None)
    with pytest.raises(transcribe.TranscribeError, match="API key"):
        transcribe.transcribe_audio(audio, make_job(), client=None)


def _fake_ffprobe_run(stdout: str):
    def _run(cmd, **kw):
        return type("R", (), {"stdout": stdout, "returncode": 0})()
    return _run


def test_transcribe_overrides_mime_for_audio_only_mp4(meeting_home, tmp_path, monkeypatch):
    """.mp4 audio-only (voice recorder 3GP direname) → mime_type default video/mp4
    bikin Gemini gagal (code 13 INTERNAL, dikonfirmasi manual) — harus di-override."""
    from meeting import paths
    paths.ensure_dirs()
    audio = tmp_path / "rekaman.mp4"
    audio.write_bytes(b"fake")
    monkeypatch.setattr(transcribe.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(transcribe.subprocess, "run", _fake_ffprobe_run(""))  # tanpa stream video

    client = FakeClient()
    transcribe.transcribe_audio(audio, make_job(), client=client)
    assert client.files.uploaded == [str(audio)]
    assert client.files.upload_configs[0] == {"mime_type": "audio/mp4"}


def test_transcribe_no_override_when_real_video_present(meeting_home, tmp_path, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    audio = tmp_path / "zoom.mp4"
    audio.write_bytes(b"fake")
    monkeypatch.setattr(transcribe.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(transcribe.subprocess, "run", _fake_ffprobe_run("video\n"))

    client = FakeClient()
    transcribe.transcribe_audio(audio, make_job(), client=client)
    assert client.files.upload_configs[0] is None


def test_transcribe_overrides_mime_for_opus_output(meeting_home, tmp_path, monkeypatch):
    """Output Opus finalizer harus di-override ke audio/ogg (bukan video/*)."""
    from meeting import paths
    paths.ensure_dirs()
    audio = tmp_path / "hasil.opus"
    audio.write_bytes(b"fake")
    monkeypatch.setattr(transcribe.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(transcribe.subprocess, "run", _fake_ffprobe_run(""))

    client = FakeClient()
    transcribe.transcribe_audio(audio, make_job(), client=client)
    assert client.files.upload_configs[0] == {"mime_type": "audio/ogg"}


def test_transcribe_no_override_when_ffprobe_missing(meeting_home, tmp_path, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    audio = tmp_path / "rekaman.mp4"
    audio.write_bytes(b"fake")
    monkeypatch.setattr(transcribe.shutil, "which", lambda name: None)

    client = FakeClient()
    transcribe.transcribe_audio(audio, make_job(), client=client)
    assert client.files.upload_configs[0] is None


# --- chunking: audio panjang dipecah jadi beberapa bagian ---------------------

def test_chunk_offsets_splits_evenly():
    offsets = transcribe._chunk_offsets(2700.0)  # 45 menit → 3 x 15 menit
    assert len(offsets) == 3
    assert [round(o[0]) for o in offsets] == [0, 900, 1800]
    assert all(round(o[1]) == 900 for o in offsets)


def test_chunk_offsets_uneven_duration_still_equal_pieces():
    offsets = transcribe._chunk_offsets(1201.0)  # sedikit di atas 20 menit → 2 bagian
    assert len(offsets) == 2
    assert offsets[0][1] == pytest.approx(600.5)
    assert offsets[1][1] == pytest.approx(600.5)


def test_prepare_chunks_below_threshold_returns_single(meeting_home, audio):
    from meeting import paths
    paths.ensure_dirs()
    chunks = transcribe._prepare_chunks(audio, "job1")
    assert chunks == [(audio, 0.0)]


def test_probe_duration_reads_flac_via_soundfile(meeting_home, audio):
    dur = transcribe._probe_duration(audio)
    assert dur == pytest.approx(0.5, abs=0.01)  # fixture: 8000 frame @ 16kHz


def test_split_soundfile_produces_correct_frame_ranges(meeting_home, audio, tmp_path):
    """audio fixture: 0.5 s @ 16kHz = 8000 frame. Pecah jadi 2 bagian 0.25 s masing2."""
    import soundfile as sf
    from meeting import paths
    paths.ensure_dirs()

    offsets = [(0.0, 0.25), (0.25, 0.25)]
    chunks = transcribe._split_soundfile(audio, offsets, "job1")
    assert len(chunks) == 2
    for c in chunks:
        assert sf.info(str(c)).frames == 4000


def test_split_ffmpeg_raises_clear_error_when_missing(meeting_home, tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe.shutil, "which", lambda name: None)
    with pytest.raises(transcribe.TranscribeError, match="ffmpeg"):
        transcribe._split_ffmpeg(tmp_path / "x.mp4", [(0.0, 10.0)], "job1")


def test_split_ffmpeg_argv_is_cpu_only(meeting_home, tmp_path, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()
    monkeypatch.setattr(transcribe.shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["env"] = kw.get("env", {})
        Path(argv[-1]).write_bytes(b"x")
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(transcribe.subprocess, "run", fake_run)
    chunks = transcribe._split_ffmpeg(tmp_path / "x.mp4", [(0.0, 1.0)], "job1")
    assert len(chunks) == 1
    assert seen["argv"][seen["argv"].index("-hwaccel") + 1] == "none"
    assert seen["argv"][seen["argv"].index("-threads") + 1] == "2"
    assert seen["env"]["MKL_NUM_THREADS"] == "1"


def test_split_ffmpeg_stream_copies_lossy_source(meeting_home, tmp_path, monkeypatch):
    from meeting import paths

    paths.ensure_dirs()
    monkeypatch.setattr(transcribe.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)
        Path(argv[-1]).write_bytes(b"x")
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(transcribe.subprocess, "run", fake_run)
    chunks = transcribe._split_ffmpeg(tmp_path / "record.opus", [(0.0, 900.0)], "streamjob")
    assert chunks[0].suffix == ".ogg"
    assert "copy" in seen[0]
    assert "-ar" not in seen[0]


def test_transcribe_splits_long_audio_and_concatenates_with_markers(
    meeting_home, audio, monkeypatch
):
    """Simulasikan audio panjang: _prepare_chunks dipatch langsung ke 2 potongan
    (mekanisme split sungguhan sudah diuji terpisah) — fokus di sini: orkestrasi
    upload/generate per potongan + gabungan hasil dengan penanda bagian."""
    from meeting import paths
    paths.ensure_dirs()

    chunk_a = paths.tmp_dir() / "chunk_a.flac"
    chunk_b = paths.tmp_dir() / "chunk_b.flac"
    chunk_a.write_bytes(b"a")
    chunk_b.write_bytes(b"b")
    monkeypatch.setattr(
        transcribe, "_prepare_chunks", lambda p, jid: [(chunk_a, 0.0), (chunk_b, 900.0)]
    )

    client = FakeClient(text="Halo, ini transkrip.")
    meta = transcribe.transcribe_audio(audio, make_job(), client=client)

    assert client.files.uploaded == [str(chunk_a), str(chunk_b)]
    assert client.models.calls == 2
    assert meta["status"] == "done"

    md = (paths.output_dir() / meta["id"] / "transcript.md").read_text(encoding="utf-8")
    assert "--- Bagian 1 (mulai ≈ 0:00) ---" in md
    assert "--- Bagian 2 (mulai ≈ 15:00) ---" in md
    assert md.count("Halo, ini transkrip.") == 2
    # potongan lokal dibersihkan setelah selesai
    assert not chunk_a.exists()
    assert not chunk_b.exists()


def _write_silence(path, seconds=1.0, sr=16000):
    import numpy as np
    import soundfile as sf
    sf.write(str(path), np.zeros(int(sr * seconds), dtype="float32"), sr, format="FLAC")


def test_is_silent_true_for_near_zero_rms(meeting_home, tmp_path):
    p = tmp_path / "silent.flac"
    _write_silence(p)
    assert transcribe._is_silent(p) is True


def test_is_silent_false_for_real_signal(meeting_home, audio):
    assert transcribe._is_silent(audio) is False  # fixture: sine wave nyata


def test_is_silent_false_for_unreadable_extension(meeting_home, tmp_path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"not real audio")
    assert transcribe._is_silent(p) is False  # tak dicek, lolos apa adanya


def test_transcribe_fails_clearly_on_silent_audio_without_calling_gemini(
    meeting_home, tmp_path
):
    """Gemini terbukti mengarang transkrip meyakinkan dari audio hening total
    (halusinasi, dikonfirmasi manual) — job harus gagal SEBELUM upload, bukan
    dapat transkrip palsu berstatus done."""
    from meeting import paths
    paths.ensure_dirs()
    silent = paths.recordings_dir() / "silent.flac"
    _write_silence(silent)

    client = FakeClient(text="Halo, ini seharusnya tidak pernah muncul.")
    with pytest.raises(transcribe.TranscribeError, match="hening"):
        transcribe.transcribe_audio(silent, make_job(), client=client)
    assert client.files.uploaded == []  # tidak pernah upload sama sekali


def test_transcribe_chunk_local_cleanup_even_on_failure(meeting_home, audio, monkeypatch):
    from meeting import paths
    paths.ensure_dirs()

    chunk_a = paths.tmp_dir() / "chunk_a.flac"
    chunk_b = paths.tmp_dir() / "chunk_b.flac"
    chunk_a.write_bytes(b"a")
    chunk_b.write_bytes(b"b")
    monkeypatch.setattr(
        transcribe, "_prepare_chunks", lambda p, jid: [(chunk_a, 0.0), (chunk_b, 900.0)]
    )

    client = FakeClient(fail_generate=True)
    with pytest.raises(transcribe.TranscribeError):
        transcribe.transcribe_audio(audio, make_job(), client=client)

    assert not chunk_a.exists()
    assert not chunk_b.exists()
