import json

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
        self.deleted = []
        self.get_calls = 0

    def upload(self, *, file, config=None):
        self.uploaded.append(str(file))
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

    def generate_content(self, *, model, contents):
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
