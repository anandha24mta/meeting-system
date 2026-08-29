# Meeting Transcriber — Design Spec

Tanggal: 2026-08-29
Status: Disetujui (desain), menunggu review spec

## Masalah

Pengguna punya jadwal meeting padat dan back-to-back, dan harus mencatat setiap meeting satu per satu secara manual. Kami membangun tool lokal yang merekam meeting, mentranskripsi otomatis via Gemini API, dan menyimpan hasilnya di disk lokal — menghilangkan beban mencatat manual.

## Tujuan & Non-Tujuan

**Tujuan:**
- Merekam meeting dari mikrofon dan/atau audio sistem (WASAPI loopback) di Windows
- Mentranskripsi file rekaman yang sudah ada (mp4 Zoom, mp3, dll.)
- Transkrip mentah (bukan ringkasan) berbahasa Indonesia
- Semua hasil disimpan lokal; hanya audio yang dikirim ke Gemini API
- Start/stop rekam seminimal friksi mungkin: satu perintah
- Transkripsi tidak pernah menghalangi recording berikutnya (jadwal mepet)

**Non-tujuan (YAGNI — sengaja tidak dibangun sekarang):**
- Ringkasan/action items otomatis (pengguna eksplisit memilih transkrip mentah saja)
- Web UI (lihat "Arah Masa Depan")
- Daemon/server permanen
- Global hotkey, system tray
- Auto-detection "meeting dimulai"
- Sinkronisasi cloud
- Speaker identification berbasis voiceprint (hanya label perkiraan dari model bila tersedia)

## Konteks Pengguna & Lingkungan

- OS: Windows (tool dijalankan native dari PowerShell/Terminal Windows; pengembangan dapat dilakukan dari WSL2 karena folder proyek di `C:\` yang terlihat dari keduanya)
- Bahasa meeting: mayoritas Indonesia
- Durasi meeting: 1–3 jam
- Sumber rekam: mic laptop ATAU headset (bergantian — ikut default input Windows), audio sistem untuk Zoom/Meet/Teams, dan file rekaman lama
- AI access: Google AI Studio API key (Gemini Developer API)

## Arsitektur

```
┌─────────────────────────────────────────────────────┐
│  Windows (PowerShell/Terminal)                      │
│                                                     │
│  meeting record ──┐                                 │
│                   ▼                                 │
│          [ Recorder ]                               │
│          sounddevice + WASAPI loopback              │
│          tulis ke disk tiap ~5 detik (crash-safe)   │
│                   │                                 │
│  Ctrl+C / meeting stop                              │
│                   ▼                                 │
│          finalisasi audio (WAV → FLAC mono 16kHz)   │
│                   │                                 │
│                   ▼                                 │
│          tulis job file → data/queue/pending/       │
│          spawn worker (detached, background)        │
│          terminal LANGSUNG BEBAS ✓                  │
│                                                     │
│          [ Worker (proses terpisah) ]               │
│          1. upload audio → Gemini File API          │
│          2. generateContent → transkrip mentah      │
│          3. simpan .md + .txt + .json ke output/    │
│          4. pindahkan job → done/ atau failed/      │
│                                                     │
│  meeting transcribe file.mp4 ──→ tulis job pending/ │
│                                                     │
│  meeting status / list / retry / devices            │
└─────────────────────────────────────────────────────┘
```

**Pendekatan: CLI + worker terpisah.** Dipilih karena kendala utama pengguna adalah jadwal meeting padat — stop rekaman harus langsung membebaskan terminal, dan transkripsi berjalan di belakang tanpa daemon permanen. Alternatif yang dipertimbangkan: CLI satu proses (terminal sibuk setelah stop — ditolak karena menghalangi meeting berikutnya) dan web app lokal (lebih lama dibangun, browsing folder dinilai cukup — ditunda, lihat Arah Masa Depan).

### Komponen & Tanggung Jawab

| Komponen | Tugas | Tidak tahu tentang |
|---|---|---|
| `recorder` | Buka device audio, tulis chunk ke disk secara incremental, finalize file | Gemini, transkripsi, antrian |
| `finalizer` | Konversi WAV → FLAC mono 16kHz | Device audio, Gemini |
| `queuer` | Tulis job JSON ke `queue/pending/`, spawn worker detached | Audio, Gemini |
| `worker` | Ambil job FIFO, upload ke Gemini, simpan hasil, pindah job ke `done/`/`failed/` | Device audio |
| `index builder` | Perbarui `INDEX.md` pada setiap transisi status | Internal modul lain |
| `config` | API key (`.env`), preferensi (`config.json`) | — |

### Struktur Data di Disk

```
meeting-system/
├── data/
│   ├── recordings/          # audio final (FLAC) — tidak pernah dihapus otomatis
│   ├── queue/
│   │   ├── pending/         # job menunggu; *.processing saat diambil worker
│   │   ├── done/            # job selesai (bukti + log)
│   │   └── failed/          # job gagal → meeting retry
│   └── output/
│       └── 2026-08-29_1400/
│           ├── transcript.md
│           ├── transcript.txt
│           └── meta.json    # durasi, device, model, status, timestamp
└── INDEX.md                 # tabel: tanggal | durasi | status transkrip | path
```

**Prinsip kunci:** audio yang sudah selesai direkam **tidak pernah dihapus** oleh tool, apa pun yang terjadi pada transkripsi. Job di `failed/` selalu punya audio utuh untuk di-retry.

## Perintah CLI

```powershell
meeting record                    # mode terakhir yang dipakai (default: mic)
meeting record --sys              # audio sistem (Zoom/Meet dari speaker)
meeting record --mic              # mikrofon
meeting record --both             # mic + audio sistem (dua stream, digabung saat finalisasi)
meeting record --name "standup"   # opsional; default = tanggal-jam

meeting transcribe "Zoom-2026-08-28.mp4"   # transkrip file yang sudah ada

meeting status        # antrian: pending/done/failed + sesi rekam aktif bila ada
meeting list          # daftar meeting dari INDEX.md, terbaru di atas
meeting retry         # kembalikan semua job failed/ ke pending/, spawn worker
meeting devices       # listing + pilih device input/output
```

### Perilaku Penting

1. `meeting record` tanpa argumen memakai mode terakhir (tersimpan di `config.json`).
2. **Anti-dobel:** sesi rekam aktif + `meeting record` lagi → ditolak dengan pesan jelas, bukan merekam dua-duanya diam-diam.
3. `meeting transcribe` meng-upload langsung format yang sudah didukung Gemini (mp4/mp3/m4a/flac/wav); konversi via `ffmpeg` hanya bila format tidak didukung dan `ffmpeg` tersedia; bila tidak tersedia, pesan error satu kalimat berisi cara install.
4. `transcript.md` diawali metadata (tanggal, durasi, device, model) lalu transkrip verbatim dengan timestamp per segmen bila model mengembalikannya.
5. `INDEX.md` diperbarui pada setiap transisi status (record selesai → pending → done/failed).

## Recording Engine

| Tahap | Format | Alasan |
|---|---|---|
| Saat merekam | WAV mono 16 kHz, ditulis tiap ~5 detik | Tanpa encoder di jalur panas; chunk kecil = crash-safe |
| Setelah stop | FLAC mono 16 kHz | Didukung Gemini, lossless, ~50% lebih kecil |
| Estimasi ukuran | WAV ≈ 55 MB/jam → FLAC ≈ 25–30 MB/jam; meeting 3 jam ≈ 90 MB | Aman untuk disk |

**Sumber audio:**
- `--mic`: `sounddevice` membuka default input Windows (mic laptop atau headset — ikut default sistem; dapat dipilih eksplisit via `meeting devices`)
- `--sys`: WASAPI loopback — kandidat library `soundcard` (pure Python) atau `pyaudiowpatch`; keputusan final melalui tes singkat di mesin pengguna saat implementasi, bukan asumsi
- `--both`: dua stream paralel → dua file WAV → digabung saat finalisasi menjadi satu FLAC (mic kiri, sistem kanan) — detail mixing diputuskan saat implementasi, target satu job transkripsi

### Edge Cases

1. **Crash/force-quit di tengah sesi** — WAV incremental tetap ada. `meeting record` berikutnya mendeteksi `_session.wav` yatim → menawarkan finalisasi & transkrip, atau buang. Tidak ada rekaman yang hilang diam-diam.
2. **Meeting back-to-back** — target terminal bebas < 10 detik setelah stop; bila finalisasi file besar terasa lambat, finalisasi dipindahkan ke worker (keputusan implementasi).
3. **Device dicabut di tengah sesi** — stream mati → rekaman dihentikan rapi (setara Ctrl+C), bukan crash korup.
4. **Disk penuh** — cek ruang sebelum mulai (~60 MB/jam); kegagalan menulis chunk = stop rapi + pesan jelas.
5. **Laptop sleep** — stream mati → stop rapi; audio sampai titik itu selamat. Mencegah sleep adalah urusan power settings Windows, bukan tool ini.
6. **Rekaman kosong/senyap** — tetap diproses; durasi tercatat di `meta.json` sehingga anomali terlihat di INDEX.md.

## Worker & Integrasi Gemini

### Siklus Hidup Worker

```
spawn (detached) saat job baru masuk
  ├─ cek lock: sudah ada worker jalan? → exit 0
  ├─ loop: ambil job tertua di queue/pending/
  │    1. rename → .processing
  │    2. files.upload(audio)
  │    3. polling hingga status file ACTIVE
  │    4. generateContent → transkrip mentah
  │    5. tulis output/<nama>/transcript.md + .txt + meta.json
  │    6. update INDEX.md (done)
  │    7. files.delete di Gemini; pindah job → done/
  │    └─ error → job → failed/ + log, lanjut job berikutnya
  └─ pending/ kosong → exit (tidak ada proses menetap)
```

Satu worker aktif kapan pun (file lock) — mencegah upload paralel dan race di INDEX.md. Pemrosesan FIFO.

### Model & Prompt

- Model: kelas `gemini-flash` (nama persis di `config.json`, dapat diganti tanpa ubah kode)
- Prompt (Bahasa Indonesia):

  > "Transkripsikan audio meeting berikut secara lengkap dan verbatim dalam bahasa Indonesia. Sertakan penanda pembicara jika teridentifikasi (Pembicara 1, Pembicara 2, dst). Jangan meringkas, jangan menambahkan interpretasi. Jika ada bagian yang tidak jelas, tulis [tidak jelas]. Format: timestamp perkiraan per segmen bila memungkinkan."

- Audio 3 jam ditangani dalam satu request via File API (batas 2 GB/file; jauh di atas kebutuhan) — tidak perlu pemotongan.

### Error Handling & Retry

| Kondisi | Perilaku |
|---|---|
| Upload gagal (network) | Job → `failed/`, log berisi penyebab |
| Rate limit / 429 | Backoff 60s lalu 5 menit, maks 2 retry otomatis dalam sesi worker |
| Gagal setelah backoff | Job tetap di `failed/`; audio aman di `recordings/` |
| Respons tanpa teks | Dianggap gagal → `failed/` (tidak menulis file kosong) |
| API key invalid | Worker exit + pesan jelas ke log; `meeting status` menampilkan penyebab |

### Keamanan & Privasi

- API key hanya di `.env` lokal (di-`.gitignore`), tidak pernah masuk log
- Audio & transkrip 100% lokal; satu-satunya trafik keluar adalah audio ke Gemini API akun pengguna sendiri
- File yang diupload dihapus dari Gemini (`files.delete`) setelah transkripsi sukses

### Konfigurasi

```
# .env
GEMINI_API_KEY=...

# config.json
{
  "model": "gemini-flash",
  "default_source": "mic",     // terakhir dipakai meeting record
  "mic_device": null           // null = default Windows
}
```

## Testing

- **Unit:** protokol antrian (tulis job → worker ambil → pindah done/failed), finalizer WAV→FLAC, retry logic — dengan audio sintetis pendek
- **Integration:** rekam 10 detik nyata → transkrip → assert `.md` ada dan berisi teks
- **Manual checklist per rilis:** `--mic`, `--sys`, `--both`, crash-kill di tengah sesi, back-to-back record, `transcribe` mp4

## Arah Masa Depan: Web App

Pengguna menyatakan kemungkinan membangun web app lokal di kemudian hari. Arsitektur ini sengaja mendukungnya tanpa rombakan:

- Inti sistem adalah **antrian berbasis folder + worker + penyimpanan file** — bukan CLI
- CLI hanyalah satu klien yang menulis job ke `queue/pending/`
- Web app kelak menjadi klien lain: UI menulis job ke antrian yang sama; worker, format job, struktur output, dan INDEX.md tidak berubah
- Satu-satunya bagian yang diganti adalah mekanisme perekaman (browser memakai `getUserMedia`/`getDisplayMedia` sendiri), yang memang sudah terisolasi di `recorder`

Karena batas antara recorder, antrian, dan worker sudah tegas, investasi pada sistem ini terlindungi saat web app dibangun.
