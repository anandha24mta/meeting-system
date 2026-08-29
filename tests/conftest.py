import json
from pathlib import Path

import pytest


@pytest.fixture
def meeting_home(tmp_path, monkeypatch):
    """Isolasi seluruh data tool ke tmp dir lewat MEETING_HOME."""
    home = tmp_path / "meeting-home"
    home.mkdir()
    monkeypatch.setenv("MEETING_HOME", str(home))
    return home


@pytest.fixture
def config(meeting_home):
    """config.json valid + API key dummy."""
    from meeting import paths

    paths.ensure_dirs()
    paths.config_file().write_text(
        json.dumps({"model": "gemini-flash", "default_source": "mic", "mic_device": None}),
        encoding="utf-8",
    )
    paths.env_file().write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")
    return paths.config_file()
