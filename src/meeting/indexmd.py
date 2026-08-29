"""INDEX.md: tabel seluruh meeting. Satu baris per id; upsert per transisi status."""
from __future__ import annotations

import re

from . import paths

_HEADER = "# INDEX\n\n| id | tanggal | durasi | status | path |\n|---|---|---|---|---|\n"
_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$")


def _row_line(r: dict) -> str:
    return f"| {r['id']} | {r['tanggal']} | {r['durasi']} | {r['status']} | {r['path']} |"


def upsert(row: dict) -> None:
    path = paths.index_file()
    if not path.exists():
        path.write_text(_HEADER + _row_line(row) + "\n", encoding="utf-8")
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    replaced = False
    sep_seen = False
    for ln in lines:
        if ln.startswith("|---"):
            sep_seen = True
            out.append(ln)
            continue
        m = _ROW_RE.match(ln)
        if sep_seen and m and m.group(1) == row["id"]:
            out.append(_row_line(row))  # update in-place
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        # sisip tepat di bawah header (terbaru di atas)
        for i, ln in enumerate(out):
            if ln.startswith("|---"):
                out.insert(i + 1, _row_line(row))
                break
        else:
            out.append(_HEADER)
            out.append(_row_line(row))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def rows() -> list[dict]:
    """Parse tabel jadi list of dict, urut file (terbaru di atas)."""
    path = paths.index_file()
    if not path.exists():
        return []
    result = []
    past_sep = False
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith("|---"):
            past_sep = True
            continue
        m = _ROW_RE.match(ln)
        if past_sep and m:
            result.append(
                {
                    "id": m.group(1),
                    "tanggal": m.group(2),
                    "durasi": m.group(3),
                    "status": m.group(4),
                    "path": m.group(5),
                }
            )
    return result


def fmt_duration(sec: float) -> str:
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
