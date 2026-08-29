from meeting import indexmd


def row(**over):
    r = {
        "id": "2026-08-29_1400-standup",
        "tanggal": "2026-08-29 14:00",
        "durasi": "1:00:00",
        "status": "pending",
        "path": "data/output/2026-08-29_1400-standup/transcript.md",
    }
    r.update(over)
    return r


def test_creates_file_with_header(meeting_home):
    indexmd.upsert(row())
    from meeting import paths
    text = paths.index_file().read_text(encoding="utf-8")
    assert text.startswith("# INDEX\n\n")
    assert "| id | tanggal | durasi | status | path |" in text
    assert "2026-08-29_1400-standup" in text


def test_upsert_updates_existing_row_in_place(meeting_home):
    from meeting import paths
    indexmd.upsert(row())
    indexmd.upsert(row(status="done"))
    text = paths.index_file().read_text(encoding="utf-8")
    # NB: kolom path juga memuat id — hitung baris data, bukan substring total
    data_lines = [ln for ln in text.splitlines() if ln.startswith("| 2026-08-29_1400-standup |")]
    assert len(data_lines) == 1
    assert "| done |" in text


def test_newest_row_inserted_below_header(meeting_home):
    from meeting import paths
    indexmd.upsert(row())
    indexmd.upsert(row(id="2026-08-29_1600", tanggal="2026-08-29 16:00"))
    lines = paths.index_file().read_text(encoding="utf-8").splitlines()
    data_lines = [ln for ln in lines if ln.startswith("| 2026")]
    assert data_lines[0].startswith("| 2026-08-29_1600")  # terbaru di atas


def test_rows_parses_table(meeting_home):
    indexmd.upsert(row(status="done"))
    rows = indexmd.rows()
    assert len(rows) == 1
    assert rows[0]["id"] == "2026-08-29_1400-standup"
    assert rows[0]["status"] == "done"


def test_fmt_duration():
    assert indexmd.fmt_duration(3661.0) == "1:01:01"
    assert indexmd.fmt_duration(59.0) == "0:59"
    assert indexmd.fmt_duration(0.0) == "0:00"
