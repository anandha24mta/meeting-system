import json

import pytest

from meeting import paths


def test_paths_honor_meeting_home(meeting_home):
    assert paths.home() == meeting_home
    assert paths.queue_dir("pending") == meeting_home / "data" / "queue" / "pending"
    assert paths.tmp_dir() == meeting_home / "data" / "tmp"
    assert paths.index_file() == meeting_home / "INDEX.md"


def test_ensure_dirs_creates_all(meeting_home):
    paths.ensure_dirs()
    for d in (
        paths.recordings_dir(),
        paths.queue_dir("pending"),
        paths.queue_dir("done"),
        paths.queue_dir("failed"),
        paths.output_dir(),
        paths.tmp_dir(),
    ):
        assert d.is_dir(), f"missing {d}"


from meeting import config


def test_load_creates_default_on_first_run(meeting_home):
    cfg = config.load()
    assert cfg["model"] == "gemini-2.5-flash"
    assert cfg["default_source"] == "mic"
    assert cfg["mic_device"] is None
    assert config_file_json_exists()


def config_file_json_exists():
    from meeting import paths
    return paths.config_file().is_file()


def test_audio_quality_keys_are_additive_defaults(meeting_home):
    cfg = config.load()
    assert cfg["codec"] == "opus"
    assert cfg["quality"] == "balanced"
    assert cfg["sample_rate"] == 16000
    assert cfg["channels"] == "auto"
    assert config.QUALITY_CHOICES == ("archive", "best", "balanced", "compact")
    assert config.CODEC_CHOICES == ("flac", "opus")


def test_old_config_without_audio_keys_still_loads(meeting_home):
    from meeting import paths
    paths.config_file().write_text(
        json.dumps({"model": "gemini-2.5-pro", "default_source": "sys", "mic_device": None}),
        encoding="utf-8",
    )
    cfg = config.load()
    assert cfg["model"] == "gemini-2.5-pro"
    assert cfg["default_source"] == "sys"
    assert cfg["codec"] == "opus"  # config lama tetap valid, key baru terisi default


def test_load_merges_file_over_defaults(meeting_home):
    from meeting import paths
    paths.config_file().write_text('{"model": "gemini-2.5-pro"}', encoding="utf-8")
    cfg = config.load()
    assert cfg["model"] == "gemini-2.5-pro"
    assert cfg["default_source"] == "mic"  # default tetap terisi


def test_load_rejects_corrupt_json(meeting_home):
    from meeting import paths
    paths.config_file().write_text("{not json", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load()


def test_update_persists_atomically(meeting_home):
    cfg = config.update(default_source="sys")
    assert cfg["default_source"] == "sys"
    assert config.load()["default_source"] == "sys"


def test_api_key_from_env_file(meeting_home):
    from meeting import paths
    assert config.api_key() is None  # belum ada .env
    paths.env_file().write_text("GEMINI_API_KEY=abc123\n", encoding="utf-8")
    assert config.api_key() == "abc123"


def test_api_key_never_leaks_into_cfg(meeting_home):
    from meeting import paths
    paths.env_file().write_text("GEMINI_API_KEY=abc123\n", encoding="utf-8")
    assert "api_key" not in config.load()


def test_load_rejects_non_dict_config(meeting_home):
    """config.json berisi array (mis. [1,2]) → ConfigError jelas, bukan
    TypeError mentah dari cfg.update()."""
    from meeting import paths
    paths.config_file().write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load()


def test_api_key_strips_surrounding_quotes(meeting_home):
    """Konvensi .env: nilai boleh dikutip — kutip bukan bagian key."""
    from meeting import paths
    paths.env_file().write_text('GEMINI_API_KEY="abc123"\n', encoding="utf-8")
    assert config.api_key() == "abc123"
    paths.env_file().write_text("GEMINI_API_KEY='abc123'\n", encoding="utf-8")
    assert config.api_key() == "abc123"
