# Checklist Manual per Rilis

Jalankan di mesin Windows target (mic/headset/speaker tersambung, .env berisi
GEMINI_API_KEY valid). Centang setiap item.

## Format & setup

Input: mp4, mp3, m4a, flac, wav, webm, ogg, mkv. Default `audio_codec: flac`,
`container: auto` — rekaman tetap FLAC. Untuk AAC/mp4/mkv: `winget install ffmpeg`
lalu set `"audio_codec": "aac"` di config.json. `auto` = m4a untuk rekaman
audio-only, container sumber untuk input mp4/mkv. VFR hanya dipertahankan pada
passthrough source mp4/mkv (`-c:v copy`); tool ini tidak melakukan video capture.

## Rekam

- [x] `meeting record --mic` — berhenti dengan Ctrl+C; job muncul di `meeting status`;
      transcript.md muncul di `data/output/<id>/` dalam beberapa menit
- [x] `meeting record --sys` — putar audio dari Zoom/Meet/YouTube; transkrip berisi
      ucapan dari audio sistem
- [ ] `meeting record --both` — FLAC final stereo (mic kiri, sistem kanan) — cek
      `data/recordings/<id>.flac` 2 kanal; transkrip tetap masuk akal
- [ ] `meeting record` (tanpa flag) — memakai mode terakhir (cek `config.json`
      `default_source` berubah setelah memakai flag)
- [ ] `meeting record --name "standup"` — folder output memakai nama

## Anti-dobel & tombol

- [ ] Tombol floating merekam → `meeting record` ditolak dengan pesan sesi aktif
- [ ] `meeting record` merekam → klik tombol ditolak (warning box)
- [ ] Klik tombol saat merekam → berhenti, ✓ tersimpan, job masuk antrian
- [ ] Posisi tombol setelah drag + tutup → diingat saat dibuka lagi
- [ ] Matikan widget (kill python) saat merekam → WAV tetap ditulis; klik record
      berikutnya menawarkan pemulihan yatim

## Ketahanan

- [ ] Crash-kill (taskkill /F) di tengah sesi → `meeting record` berikutnya
      menawarkan finalisasi rekaman yatim
- [ ] Tolak recovery → WAV pindah ke `data/tmp/discarded/` (tidak dihapus)
- [ ] Cabut headset saat merekam → rekaman berhenti rapi, job tetap dibuat
- [ ] Disk hampir penuh → pesan jelas sebelum mulai merekam
- [ ] Tekan Ctrl+C saat `meeting record --both` → terminal bebas dalam hitungan detik
      dan kedua stream (mic + sistem) berhenti bersama; FLAC final tetap stereo valid

## Transkripsi file

- [ ] `meeting transcribe Zoom.mp4` → job langsung (tanpa ffmpeg)
- [ ] File format eksotis tanpa ffmpeg → pesan install satu kalimat
- [ ] (Opsional, ffmpeg terpasang) format eksotis dikonversi otomatis
- [ ] Meeting panjang (>20 menit) → transcript.md lengkap sampai akhir,
      ada penanda "--- Bagian N (mulai ≈ ...) ---" per potongan

## Codec & container (opt-in)

- [ ] Set `"audio_codec": "aac"`, `"container": "m4a"` di config.json →
      `meeting record` 10 detik → `data/recordings/<id>.m4a` bisa diputar di
      Windows Media Player/VLC (AAC-LC, bukan HE-AAC); `meta.json` berisi
      `codec: aac`, `container: m4a`, `vbr: true`, `bitrate` ≈ 64000 mono
- [ ] File m4a hasil rekaman di-upload Gemini tanpa error mime/code 13 —
      `meeting status` done, `transcript.md` berisi ucapan nyata (bukan kosong)
- [ ] `meeting transcribe zoom.mp4` dengan `audio_codec: aac` → video stream
      ikut ter-remux byte-identical: bandingkan
      `ffmpeg -i asli.mp4 -map 0:v -c copy -f md5 -` dengan hasil di `data/tmp/`;
      `meta.json` berisi `video_passthrough: true`, `vfr: true`; VFR tidak
      berubah (durasi & timestamp sama), tidak ada re-encode video
- [ ] Ulangi untuk `.mkv` dengan video VFR → sama, container tetap mkv
- [ ] Tanpa ffmpeg di PATH: `meeting record` tetap menghasilkan `.flac` dan
      transkrip selesai; `meeting transcribe file.mp4` langsung diproses;
      format eksotis memberi pesan satu kalimat `winget install ffmpeg`

## Worker

- [ ] Dua job cepat berturut → diproses FIFO, satu worker (cek `data/worker.log`)
- [ ] Putuskan jaringan saat job → job ke `failed/`; `meeting retry` setelah
      jaringan kembali → selesai
- [ ] API key salah → `meeting status` menampilkan penyebab
- [ ] File Gemini terhapus setelah sukses (cek di AI Studio "File Manager" bila mau)

## Back-to-back

- [ ] Stop rekaman → terminal bisa langsung dipakai `meeting record` lagi (<10 s)
- [ ] Dua meeting berturut → dua job di antrian diproses berurutan
