# Meeting Transcriber

Rekam meeting di Windows dan transkripsi otomatis via Gemini — semua hasil lokal.

## Setup

    pip install -e .
    copy .env.example .env    # isi GEMINI_API_KEY (Google AI Studio)
    winget install ffmpeg     # direkomendasikan — default AAC/m4a & format eksotis

Default: WAV direkam lalu **langsung** di-encode sekali ke AAC-LC VBR di `.m4a`
(tanpa perantara FLAC). Tanpa ffmpeg, finalizer otomatis jatuh ke FLAC dan
rekaman tetap berhasil.

## Format

Input didukung langsung: `mp4`, `mp3`, `m4a`, `flac`, `wav`, `webm`, `ogg`,
`mkv`. Format lain dikonversi via ffmpeg bila terpasang.

| Sumber | Default | Opt-in `audio_codec: flac` |
|---|---|---|
| `meeting record` (audio-only) | AAC-LC VBR di `.m4a`, langsung dari WAV | FLAC 16 kHz lossless |
| file `.m4a`/`.mp4`/`.mkv` | remux: video `-c:v copy`, audio AAC-LC VBR | file asli dipakai apa adanya |
| file audio lain | file asli dipakai apa adanya | FLAC/AAC sesuai opts |

`config.json`:

    "audio_codec": "aac" | "flac"     (default "aac")
    "container":   "auto" | "m4a" | "mp4" | "mkv"   (default "auto")

`auto` = `.m4a` untuk rekaman audio-only, container sumber untuk input mp4/mkv.
VBR memakai `-q:a` (native ffmpeg AAC) atau `-vbr` bila `libfdk_aac` tersedia;
target ~64 kbps mono / ~128 kbps stereo. Tidak ada video capture di tool ini —
video hanya berasal dari file sumber, dan VFR hanya dipertahankan pada jalur
passthrough (`-c:v copy`), bukan pada rekaman audio-only. Rekaman audio-only
tetap mono/stereo seperti sebelumnya.

## Pakai

    meeting record            # mode terakhir (default mic)
    meeting record --sys      # audio sistem (Zoom/Meet dari speaker)
    meeting record --both     # mic + sistem
    meeting transcribe file.mp4
    meeting status            # antrian + sesi aktif
    meeting list              # INDEX.md
    meeting retry             # failed → antrian
    meeting devices           # listing / --mic "Nama"

Hasil: `data/output/<id>/transcript.md` + `INDEX.md`. Audio final di `data/recordings/` — tidak pernah dihapus otomatis.

## Floating record button

    meeting button

Auto-start saat login (opsional): buat shortcut `meeting button` di
`shell:startup` (Win+R → `shell:startup`).

## Troubleshooting

- `ffmpeg` dibutuhkan untuk format eksotis, AAC/mp4/mkv, dan untuk memecah
  meeting panjang (>20 menit) yang formatnya bukan WAV/FLAC:
  `winget install ffmpeg`. Tanpa ffmpeg, hasil rekaman tetap FLAC dan file
  sumber yang didukung tetap diproses langsung
- Worker log: `data/worker.log`; error terakhir: `meeting status`
