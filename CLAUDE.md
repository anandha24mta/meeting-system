# CLAUDE.md

## Changelog & versioning

Setiap commit yang mengandung perubahan user-facing (fitur baru, bug fix,
perubahan perilaku) harus disertai entri baru di `CHANGELOG.md` (format
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/)) dan bump versi di
`pyproject.toml` (`[project].version`) — patch untuk bug fix, minor untuk
fitur baru, mengikuti [Semantic Versioning](https://semver.org/). Commit
internal murni (docs, chore, refactor tanpa perubahan perilaku) tidak perlu
entri baru — tambahkan ke entri versi yang sedang berjalan bila relevan.
