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

## Resource & tuning

Semua transkripsi tetap lewat Gemini — tidak ada model lokal, GPU tidak dipakai
untuk transkripsi. Worker hanya ada saat antrean tidak kosong.

`config.json` (opsional, semua punya default):

    max_workers           4       # worker paralel maksimum (dibatasi cpu//2 dan RAM)
    max_parallel_chunks   2       # potongan meeting panjang yang diproses bersamaan
    per_job_memory_mb     256     # anggaran RAM per worker untuk pembatasan
    thread_cap            2       # OMP/MKL/OPENBLAS_NUM_THREADS
    flush_interval_sec    5       # interval flush WAV saat merekam (detik)
    gpu_mode              auto    # auto|off — hanya deteksi kapabilitas, malas
    analysis_device       cpu     # cpu|gpu — GPU hanya untuk RMS audio (butuh CuPy)

Contoh: `meeting record --max-workers 2`. Benchmark lokal: `python tools/bench_resources.py --out data/bench/resources.json`.

Deteksi GPU hanya membaca kapabilitas (Intel QSV / NVIDIA NVENC / AMD VCN) dan
tidak mengimpor library GPU. `analysis_device=gpu` memakai CuPy bila tersedia
dan jatuh ke CPU bila tidak — ini tidak mengubah jalur Gemini.

## Floating record button

    meeting button

Auto-start saat login (opsional): buat shortcut `meeting button` di
`shell:startup` (Win+R → `shell:startup`).

## Troubleshooting

- `ffmpeg` dibutuhkan untuk format eksotis, dan juga untuk memecah meeting
  panjang (>20 menit) yang formatnya bukan WAV/FLAC: `winget install ffmpeg`
  (meeting hasil `meeting record` selalu FLAC — tidak butuh ffmpeg untuk ini)
- Worker log: `data/worker.log`; error terakhir: `meeting status`
