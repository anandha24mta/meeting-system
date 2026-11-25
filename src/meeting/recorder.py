"""Rekam mic (sounddevice) / loopback (soundcard) / keduanya → WAV incremental 16k.
Backend di-import lazy; stop-flag file = sinyal stop lintas proses (CLI & tombol)."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from . import config, paths, session

SAMPLE_RATE = 16000
BYTES_PER_HOUR_MONO_16BIT = 115 * 1024 * 1024  # ≈ 32 kB/s
DEFAULT_FLUSH_INTERVAL_SEC = 5.0


class RecorderError(RuntimeError):
    pass


def check_disk(wav_dir: Path, est_hours: float, streams: int) -> None:
    free = shutil.disk_usage(wav_dir).free
    need = BYTES_PER_HOUR_MONO_16BIT * streams * max(est_hours, 0.1)
    if free < need:
        raise RecorderError(
            f"ruang disk tidak cukup: butuh ≈{int(need / 1024 / 1024)} MB, "
            f"tersedia {int(free / 1024 / 1024)} MB. Kosongkan disk atau pakai durasi lebih pendek."
        )


def _writer(path: Path) -> sf.SoundFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    return sf.SoundFile(str(path), mode="w", samplerate=SAMPLE_RATE,
                        channels=1, subtype="PCM_16", format="WAV")


class _Stop(Exception):
    pass


class _TimedWriter:
    """Flush maksimal sekali per interval; final flush tetap di finally."""

    def __init__(self, handle, interval: float) -> None:
        self.handle = handle
        self.interval = max(0.0, float(interval))
        self.last = time.monotonic()

    def write(self, data) -> None:
        self.handle.write(data)
        if time.monotonic() - self.last >= self.interval:
            self.flush()

    def flush(self) -> None:
        self.handle.flush()
        self.last = time.monotonic()


def _flush_interval() -> float:
    try:
        return float(config.load().get("flush_interval_sec", DEFAULT_FLUSH_INTERVAL_SEC))
    except Exception:
        return DEFAULT_FLUSH_INTERVAL_SEC


def _record_mic(job_id: str, wav: Path, mic_device: str | None) -> float:
    """sounddevice blocking stream; return durasi detik. Raise _Stop bila device hilang."""
    import sounddevice as sd

    frames = [0]

    def callback(indata, frame_count, time_info, status):
        writer.write(indata.copy())
        frames[0] += frame_count

    f = _writer(wav)
    writer = _TimedWriter(f, _flush_interval())
    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            callback=callback, device=mic_device,
        ) as stream:
            stream.start()
            while not session.stop_requested():
                time.sleep(0.2)
    except sd.PortAudioError as e:
        f.close()
        raise _Stop(f"mic device error: {e}") from e
    finally:
        try:
            writer.flush()
        except Exception:
            pass
        f.close()
    return frames[0] / SAMPLE_RATE


def _record_sys(job_id: str, wav: Path) -> float:
    """soundcard WASAPI loopback; return durasi detik.
    Recorder soundcard bukan iterable — baca blok lewat record(numframes=...) yang blocking."""
    import soundcard as sc

    blocksize = 8000
    spk = sc.default_speaker()
    mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
    f = _writer(wav)
    writer = _TimedWriter(f, _flush_interval())
    n = 0
    try:
        with mic.recorder(samplerate=SAMPLE_RATE, blocksize=blocksize,
                          channels=1, exclusive_mode=False) as rec:
            while not session.stop_requested():
                block = rec.record(numframes=blocksize)
                writer.write(np.asarray(block, dtype=np.float32).reshape(-1, 1))
                n += len(block)
    except Exception as e:  # termasuk device dicabut
        raise _Stop(f"loopback error: {e}") from e
    finally:
        try:
            writer.flush()
        except Exception:
            pass
        f.close()
    return n / SAMPLE_RATE


def record(source: str, job_id: str, mic_device: str | None = None) -> dict:
    """Rekam hingga stop-flag. Return meta utk session/CLI."""
    paths.ensure_dirs()
    tmp = paths.tmp_dir()
    device = "mic default"
    lost = False

    if source == "mic":
        wav = tmp / f"{job_id}_mic.wav"
        try:
            dur = _record_mic(job_id, wav, mic_device)
        except _Stop as e:
            lost = True
            dur = _frames_of(wav) / SAMPLE_RATE
            device = str(e)
        if not lost:
            device = mic_device or "mic default"
        wavs = [wav]
    elif source == "sys":
        wav = tmp / f"{job_id}_sys.wav"
        try:
            dur = _record_sys(job_id, wav)
        except _Stop as e:
            lost = True
            dur = _frames_of(wav) / SAMPLE_RATE
            device = str(e)
        if not lost:
            import sys as _s
            if "soundcard" in _s.modules:
                device = f"{_s.modules['soundcard'].default_speaker().name} loopback"
        wavs = [wav]
    elif source == "both":
        wav_m = tmp / f"{job_id}_mic.wav"
        wav_s = tmp / f"{job_id}_sys.wav"
        errs: list[Exception] = []

        def run_mic():
            try:
                _record_mic(job_id, wav_m, mic_device)
            except Exception as e:  # _Stop, disk-full, import gagal — semua berhenti rapi
                errs.append(e)

        def run_sys():
            try:
                _record_sys(job_id, wav_s)
            except Exception as e:
                errs.append(e)

        tm = threading.Thread(target=run_mic)
        ts = threading.Thread(target=run_sys)
        tm.start(); ts.start()
        while (tm.is_alive() or ts.is_alive()) and not session.stop_requested() and not errs:
            time.sleep(0.2)
        if errs:
            lost = True
            session.request_stop()  # satu stream mati → hentikan juga satunya
            tm.join(timeout=5); ts.join(timeout=5)
        else:
            tm.join(); ts.join()
        dur = min(_frames_of(wav_m), _frames_of(wav_s)) / SAMPLE_RATE
        wavs = [wav_m, wav_s]
    else:
        raise RecorderError(f"sumber tidak dikenal: {source!r} (mic|sys|both)")

    wavs = [w for w in wavs if w.exists() and _frames_of(w) > 0]
    return {
        "duration_sec": dur,
        "wav_paths": wavs,
        "device": device,
        "device_lost": lost,
    }


def _frames_of(wav: Path) -> int:
    try:
        return sf.info(wav).frames
    except Exception:
        return 0
