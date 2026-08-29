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
