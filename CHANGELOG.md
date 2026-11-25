# Changelog

Semua perubahan penting pada proyek ini didokumentasikan di sini. Format
mengikuti [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), versi
mengikuti [Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-11-25

### Ditambahkan
- Profil kualitas audio lewat `config.json`: `codec` (`flac`/`opus`), `quality`
  (`archive`/`best`/`balanced`/`compact`), `sample_rate`, `channels`. Opus memakai
  `libopus` VBR, `-application voip`, `-compression_level 10` — CPU-only, input
  WAV dibaca langsung oleh ffmpeg (memory bounded)
- Fallback otomatis ke FLAC bila ffmpeg absen atau output Opus gagal verify;
  WAV sumber tidak pernah dihapus sebelum verify sukses
- Guard CPU-only di semua perintah ffmpeg: `-hwaccel none`, `-threads 2`,
  `OMP/MKL/OPENBLAS_NUM_THREADS=1`
- `tools/bench_codecs.py` — benchmark ukuran/durasi FLAC vs Opus, hasil JSON
  ditulis ke file (bukan stdout)

### Diubah
- Default model Gemini `gemini-2.5-flash` (`gemini-flash-latest` sudah 404)
- Default finalisasi kini Opus VBR `balanced` (bukan FLAC): ≈14 MB/jam mono
  vs ≈55 MB/jam FLAC, kualitas ucapan terjaga lewat mode `voip` + VBR.
  FLAC lossless tetap tersedia via `codec: "flac", quality: "archive"`
- Output `.opus` dikirim ke Gemini dengan mime `audio/ogg`; Gemini tetap
  satu-satunya backend transkripsi (tanpa model lokal / GPU inference)

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
