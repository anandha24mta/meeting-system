# Changelog

Semua perubahan penting pada proyek ini didokumentasikan di sini. Format
mengikuti [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), versi
mengikuti [Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-11-25

### Ditambahkan
- Deteksi kapabilitas GPU/iGPU malas (cache 24 jam, tanpa dependency GPU)
- Opsi `analysis_device=gpu` untuk RMS audio — fallback otomatis ke CPU bila
  CuPy/OpenCL tidak tersedia; default `cpu`
- Opsi `--max-workers` pada `record`/`transcribe` dan config `max_workers`,
  `max_parallel_chunks`, `per_job_memory_mb`, `thread_cap`,
  `flush_interval_sec`, `gpu_mode`
- Heartbeat kepemilikan per job (`.owner`) agar beberapa worker bisa jalan
  bersamaan tanpa mengambil alih job yang masih aktif

### Diubah
- Default model Gemini `gemini-2.5-flash` (`gemini-flash-latest` sudah 404)
- Worker tidak lagi memakai lock global: jumlah worker dibatasi oleh CPU/RAM
  dan kedalaman antrean, lalu exit saat antrean habis
- Potongan audio panjang ditranskripsi paralel (default 2) dengan hasil tetap
  disusun sesuai urutan waktu
- Finalisasi, deteksi hening, dan pemecahan chunk diproses per blok — RAM tidak
  lagi sebanding dengan durasi meeting
- WAV di-flush tiap 5 detik (dulu tiap blok) — lebih ringan I/O/CPU
- `INDEX.md` dan `worker.last_error` ditulis lewat lock atomik

## [0.2.0] - 2026-09-02

Perbaikan dari QA manual di mesin Windows nyata (bukan cuma unit test).

### Ditambahkan
- Transkripsi meeting panjang (>20 menit) otomatis dipecah jadi beberapa
  bagian ~15 menit, ditranskrip terpisah, digabung dengan penanda bagian —
  generate_content Gemini terbukti berhenti acak sebelum audio benar-benar
  habis untuk verbatim panjang dalam satu panggilan
- Deteksi audio nyaris hening total sebelum upload — Gemini terbukti
  mengarang transkrip meyakinkan alih-alih melaporkan tidak ada suara
- Menu klik-kanan "Keluar" di tombol floating (sebelumnya cuma bisa lewat
  Task Manager)

### Diperbaiki
- Default model Gemini (`gemini-flash`) bukan model id valid — semua
  transkripsi gagal 404; diganti ke `gemini-flash-latest`
- `meeting record --sys` dan `--both` tidak pernah berhasil merekam audio
  sistem sama sekali sejak awal (`Recorder` dari `soundcard` bukan iterable)
- File audio-only berekstensi video (`.mp4`/`.webm` — mis. rekaman voice
  recorder Android) gagal diproses Gemini karena mime-type salah tebak dari
  ekstensi

### Diubah
- Ukuran tombol floating diperkecil 56px → 36px

## [0.1.0] - 2026-08-30

Rilis awal.

### Ditambahkan
- `meeting record` — rekam mic / audio sistem (loopback) / keduanya
- `meeting transcribe <file>` — transkripsi file yang sudah ada, konversi
  otomatis via ffmpeg untuk format eksotis
- `meeting status` / `list` / `retry` / `devices`
- Worker background: antrian FIFO, retry dengan backoff untuk rate limit,
  finalisasi WAV → FLAC sebelum transkripsi
- Pemulihan sesi rekaman yatim setelah crash/force-quit
- Tombol floating always-on-top dengan drag posisi
