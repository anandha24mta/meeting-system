# Meeting Transcriber

Rekam meeting di Windows dan transkripsi otomatis via Gemini — semua hasil lokal.

## Setup

    pip install -e .
    copy .env.example .env    # isi GEMINI_API_KEY (Google AI Studio)

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

## Kualitas audio (CPU-only)

Default `archive` = FLAC lossless 16 kHz — perilaku lama tidak berubah. Untuk file
lebih kecil, set di `config.json`:

    "codec": "opus",
    "quality": "balanced",       # best | balanced | compact

| quality  | bitrate mono/stereo | ≈ MB/jam mono/stereo |
| archive  | FLAC lossless       | ~55 / ~110           |
| best     | 64 / 128 kbps       | ~29 / ~58            |
| balanced | 32 / 64 kbps        | ~14 / ~29            |
| compact  | 24 / 48 kbps        | ~11 / ~22            |

Kunci tambahan lain: `sample_rate` (default 16000) dan `channels` (`auto`, atau
`1`/`2`). Gemini tetap satu-satunya backend transkripsi — tidak ada model lokal,
tidak ada inferensi GPU. Semua perintah ffmpeg memakai `-hwaccel none`,
`-threads 2`, dan `OMP/MKL/OPENBLAS_NUM_THREADS=1`.

ffmpeg opsional. Bila tidak ada, atau output Opus gagal verify (decode/durasi),
finalizer otomatis memakai FLAC dengan pesan jelas di stderr. WAV sumber dihapus
HANYA setelah output terverifikasi bisa dibaca — audio aman dulu, bersih belakangan.

## Floating record button

    meeting button

Auto-start saat login (opsional): buat shortcut `meeting button` di
`shell:startup` (Win+R → `shell:startup`).

## Troubleshooting

- `ffmpeg` dibutuhkan untuk format eksotis, codec Opus, dan memecah meeting
  panjang (>20 menit) yang formatnya bukan WAV/FLAC: `winget install ffmpeg`
  (tanpa ffmpeg, profil Opus otomatis pakai FLAC — rekaman tetap berhasil)
- Worker log: `data/worker.log`; error terakhir: `meeting status`
