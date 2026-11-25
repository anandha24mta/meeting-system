import threading
import time

import pytest

from meeting import config, paths, transcribe


class OrderedClient:
    def __init__(self):
        self.active = 0
        self.peak = 0
        self.guard = threading.Lock()

    def _enter(self):
        with self.guard:
            self.active += 1
            self.peak = max(self.peak, self.active)

    def _leave(self):
        with self.guard:
            self.active -= 1

    @property
    def files(self):
        return self

    @property
    def models(self):
        return self

    def upload(self, *, file, config=None):
        self._enter()
        return type("F", (), {"name": f"files/{file}"})()

    def get(self, *, name):
        return type("F", (), {"name": name, "state": "ACTIVE"})()

    def delete(self, *, name):
        self._leave()

    def generate_content(self, *, model, contents):
        name = contents[1].name
        key = next(k for k in ("a", "b", "c") if f"chunk_{k}" in name)
        delay = {"a": 0.05, "b": 0.01, "c": 0.03}[key]
        time.sleep(delay)
        return type("R", (), {"text": f"teks-{key}"})()


@pytest.fixture
def chunks(meeting_home, tmp_path, monkeypatch):
    paths.ensure_dirs()
    made = []
    for name in ("a", "b", "c"):
        p = paths.tmp_dir() / f"chunk_{name}.flac"
        p.write_bytes(name.encode())
        made.append(p)
    monkeypatch.setattr(
        transcribe, "_prepare_chunks",
        lambda path, job_id: [(p, i * 900.0) for i, p in enumerate(made)],
    )
    return made


def test_chunks_transcribed_concurrently_with_bounded_workers(meeting_home, chunks):
    config.update(max_parallel_chunks=2)
    client = OrderedClient()
    job = {
        "id": "parallel-test", "created_at": "2026-08-29T14:00:00",
        "source": "mic", "model": "gemini-flash", "status": "pending",
    }
    meta = transcribe.transcribe_audio(chunks[0], job, client=client)
    assert meta["status"] == "done"
    assert client.peak == 2  # dua konkuren, tidak tiga

    text = (paths.output_dir() / job["id"] / "transcript.txt").read_text(encoding="utf-8")
    assert text.index("teks-a") < text.index("teks-b") < text.index("teks-c")
