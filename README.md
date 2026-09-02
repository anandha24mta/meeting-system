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

## Floating record button

    meeting button

Auto-start saat login (opsional): buat shortcut `meeting button` di
`shell:startup` (Win+R → `shell:startup`).

## Troubleshooting

- `ffmpeg` dibutuhkan untuk format eksotis, dan juga untuk memecah meeting
  panjang (>20 menit) yang formatnya bukan WAV/FLAC: `winget install ffmpeg`
  (meeting hasil `meeting record` selalu FLAC — tidak butuh ffmpeg untuk ini)
- Worker log: `data/worker.log`; error terakhir: `meeting status`
