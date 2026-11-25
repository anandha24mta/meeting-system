# Changelog

Semua perubahan penting pada proyek ini didokumentasikan di sini. Format
mengikuti [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), versi
mengikuti [Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-11-25

### Ditambahkan
- Opsi `audio_codec: aac|flac` + `container: auto|m4a|mp4|mkv` di `config.json`.
  Default `aac`/`auto` — rekaman audio-only langsung jadi `.m4a`
- Encoder AAC-LC via ffmpeg (`-q:a` native, `-vbr` bila `libfdk_aac` ada),
  target ~64 kbps mono / ~128 kbps stereo. WAV sumber di-encode **sekali**
  (tanpa FLAC perantara), diverifikasi decode penuh sebelum WAV dihapus
- Passthrough video untuk input mp4/mkv saat AAC di-opt-in: `-c:v copy`
  (video byte-identical, VFR tetap utuh), tanpa re-encode dan tanpa video capture
- Preflight ffmpeg di CLI dengan pesan `winget install ffmpeg`; fallback ke FLAC
  bila ffmpeg tidak ada
- Pemulihan sesi yatim juga menghasilkan `.m4a` langsung dari WAV (bukan FLAC)
- Field baru di `meta.json`: `codec`, `container`, `bitrate`, `vbr`, `vfr`,
  `video_passthrough` (additive, backward compatible)
- Dukungan input `.mkv`

### Catatan
- VFR hanya berlaku untuk jalur passthrough source mp4/mkv, bukan rekaman
  audio-only (tidak ada video stream yang dihasilkan tool ini)

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
