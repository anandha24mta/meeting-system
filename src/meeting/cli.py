"""CLI entry point. Semua perintah tipis — logika di modul domain."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config, devices, indexmd, paths, queuer, recorder, recover, session

SUPPORTED_AUDIO = {".mp4", ".mp3", ".m4a", ".flac", ".wav", ".webm", ".ogg"}


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def cmd_record(args) -> int:
    paths.ensure_dirs()  # tmp/ dkk harus ada sebelum check_disk (statvfs butuh path)
    cfg = config.load()
    source = args.sys and "sys" or args.mic and "mic" or args.both and "both" or cfg["default_source"]
    if args.sys or args.mic or args.both:
        config.update(default_source=source)

    # pemulihan sesi yatim dulu (bila ada) — jangan tumpang tindih
    recover.offer_recovery(ui_confirm=_confirm)

    job_id = queuer.new_job_id(args.name)
    if not queuer.is_valid_id(job_id):
        _eprint(f"nama tidak valid: {job_id!r} (hanya huruf/angka/-/_)")
        return 1

    est_hours = 3.0
    try:
        recorder.check_disk(paths.tmp_dir(), est_hours=est_hours,
                            streams=2 if source == "both" else 1)
    except recorder.RecorderError as e:
        _eprint(str(e))
        return 1

    cfg = config.load()
    try:
        session.start({"source": source, "id": job_id, "wav_paths": [], "device": cfg.get("mic_device") or "default"})
    except session.SessionActiveError as e:
        _eprint(str(e))
        return 1

    print(f"Merekam ({source}) … Ctrl+C untuk stop. Job: {job_id}")
    try:
        meta = recorder.record(source, job_id, mic_device=cfg.get("mic_device"))
    except KeyboardInterrupt:
        # Ctrl+C: set stop-flag lalu TUNGGU thread recorder berhenti sebelum
        # finish() menghapus flag — tanpa ini, thread --both (poll 0.2-0.5 s)
        # tidak sempat melihat flag dan merekam selamanya (terminal tak bebas).
        session.request_stop()
        time.sleep(1.0)  # beri waktu loop poll kedua stream melihat flag & close WAV
        meta = _meta_from_disk(job_id, source)

    if not meta["wav_paths"]:
        _eprint("tidak ada audio terekam — sesi dibatalkan")
        session.finish()
        return 1

    wav_rel = [str(w.relative_to(paths.home() / "data")) for w in meta["wav_paths"]]
    session.finish()
    durasi = meta["duration_sec"]
    job = {
        "id": job_id,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,
        "audio": f"recordings/{job_id}.flac",  # target; worker isi setelah finalisasi
        "wav": wav_rel[0] if len(wav_rel) == 1 else wav_rel,
        "original_path": None,
        "needs_finalization": True,
        "duration_sec": durasi,
        "device": meta["device"],
        "model": cfg["model"],
        "status": "pending",
    }
    queuer.enqueue(job)
    indexmd.upsert(
        {
            "id": job_id,
            "tanggal": job["created_at"].replace("T", " "),
            "durasi": indexmd.fmt_duration(durasi),
            "status": "pending",
            "path": f"data/output/{job_id}/transcript.md",
        }
    )
    queuer.spawn_worker()
    print(f"Selesai merekam ({indexmd.fmt_duration(durasi)}). Job antrean: {job_id}")
    print(f"Terminal bebas — transkripsi jalan di background. Lihat: meeting status")
    return 0


def _confirm(msg: str) -> bool:
    try:
        ans = input(f"{msg} ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes", "ya")


def _meta_from_disk(job_id: str, source: str) -> dict:
    """Setelah Ctrl+C: bangun meta dari WAV incremental yang ada di tmp/."""
    import soundfile as sf

    suffixes = {"mic": ["_mic"], "sys": ["_sys"], "both": ["_mic", "_sys"]}.get(source, ["_mic", "_sys"])
    wavs = []
    for suf in suffixes:
        p = paths.tmp_dir() / f"{job_id}{suf}.wav"
        if p.exists():
            wavs.append(p)
    dur = 0.0
    for w in wavs:
        try:
            dur = max(dur, sf.info(w).frames / 16000)
        except Exception:
            pass
    return {"duration_sec": dur, "wav_paths": wavs, "device": "unknown", "device_lost": False}


def cmd_transcribe(args) -> int:
    src = Path(args.file)
    if not src.exists():
        _eprint(f"file tidak ditemukan: {src}")
        return 1
    ext = src.suffix.lower()
    cfg = config.load()
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", Path(src).stem)[:40]
    job_id = queuer.new_job_id(stem or None)
    if not queuer.is_valid_id(job_id):
        _eprint(f"nama file tidak bisa dijadikan id job: {job_id!r}")
        return 1
    audio_rel: str
    needs_fin = False

    if ext in SUPPORTED_AUDIO:
        audio_rel = str(src.resolve())  # absolut; file asli tak pernah diubah
    else:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            _eprint("Format ini butuh ffmpeg — install dengan: winget install ffmpeg")
            return 1
        paths.ensure_dirs()
        out = paths.tmp_dir() / f"{job_id}{ext}.flac"
        rc = subprocess.run(
            [ffmpeg, "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(out)],
            capture_output=True,
        )
        if rc.returncode != 0 or not out.exists():
            _eprint(f"ffmpeg gagal mengonversi {src.name}")
            return 1
        audio_rel = str(out.resolve())

    job = {
        "id": job_id,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "file",
        "audio": audio_rel,
        "wav": None,
        "original_path": str(src.resolve()),
        "needs_finalization": needs_fin,
        "duration_sec": None,
        "device": None,
        "model": cfg["model"],
        "status": "pending",
    }
    queuer.enqueue(job)
    indexmd.upsert(
        {
            "id": job_id,
            "tanggal": job["created_at"].replace("T", " "),
            "durasi": "-",
            "status": "pending",
            "path": f"data/output/{job_id}/transcript.md",
        }
    )
    queuer.spawn_worker()
    print(f"Job dibuat: {job_id} — meeting status untuk progres")
    return 0


def cmd_status(args) -> int:
    paths.ensure_dirs()
    pending = len(list(paths.queue_dir("pending").glob("*.json"))) + len(
        list(paths.queue_dir("pending").glob("*.processing"))
    )
    done = len(list(paths.queue_dir("done").glob("*.json")))
    failed = len(list(paths.queue_dir("failed").glob("*.json")))
    print(f"antrian — pending: {pending} | done: {done} | failed: {failed}")

    act = session.active()
    if act:
        print(f"sesi rekam aktif: id {act.get('id')} (mulai {act.get('started_at')})")

    le = paths.home() / "data" / "worker.last_error"
    if le.exists():
        print(f"worker error terakhir: {le.read_text(encoding='utf-8').strip()}")
    return 0


def cmd_list(args) -> int:
    rows = indexmd.rows()
    if not rows:
        print("belum ada meeting — INDEX.md kosong")
        return 0
    print(f"{'tanggal':<18}{'durasi':>8}  {'status':<8} id")
    for r in rows:
        print(f"{r['tanggal']:<18}{r['durasi']:>8}  {r['status']:<8} {r['id']}")
    return 0


def cmd_retry(args) -> int:
    paths.ensure_dirs()
    moved = 0
    for f in paths.queue_dir("failed").glob("*.json"):
        job = json.loads(f.read_text(encoding="utf-8"))
        job["status"] = "pending"
        job.pop("error", None)
        job.pop("finished_at", None)
        (paths.queue_dir("pending") / f.name).write_text(
            json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        f.unlink()
        moved += 1
    if moved:
        queuer.spawn_worker()
    print(f"{moved} job dikembalikan ke antrian")
    return 0


def cmd_devices(args) -> int:
    if args.mic is not None and args.mic != "none":
        devices.set_mic(args.mic)
        print(f"mic disetel: {args.mic}")
        return 0
    if args.mic == "none":
        devices.set_mic(None)
        print("mic disetel: default Windows")
        return 0
    for d in devices.list_devices():
        tag = "loopback" if d["kind"] == "loopback" else "mic"
        print(f"[{tag}] {d['name']}")
    print("\nSet mic:   meeting devices --mic \"Nama Mic\"")
    print("Reset default: meeting devices --mic none")
    return 0


def cmd_button(args) -> int:
    from . import button  # lazy: tkinter hanya saat dipakai

    button.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="meeting", description="Rekam & transkripsi meeting")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("record", help="rekam meeting (default: mode terakhir)")
    pr.add_argument("--sys", action="store_true", help="audio sistem (loopback)")
    pr.add_argument("--mic", action="store_true", help="mikrofon")
    pr.add_argument("--both", action="store_true", help="mic + audio sistem")
    pr.add_argument("--name", default=None, help="nama job (default: tanggal-jam)")
    pr.set_defaults(func=cmd_record)

    pt = sub.add_parser("transcribe", help="transkrip file yang sudah ada")
    pt.add_argument("file")
    pt.set_defaults(func=cmd_transcribe)

    ps = sub.add_parser("status", help="status antrian + sesi")
    ps.set_defaults(func=cmd_status)

    pl = sub.add_parser("list", help="daftar meeting dari INDEX.md")
    pl.set_defaults(func=cmd_list)

    prr = sub.add_parser("retry", help="kembalikan job failed ke antrian")
    prr.set_defaults(func=cmd_retry)

    pd = sub.add_parser("devices", help="listing / pilih device")
    pd.add_argument("--mic", default=None, help="nama mic (atau 'none' utk reset)")
    pd.set_defaults(func=cmd_devices)

    pb = sub.add_parser("button", help="floating record button")
    pb.set_defaults(func=cmd_button)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _eprint("\nDibatalkan.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
