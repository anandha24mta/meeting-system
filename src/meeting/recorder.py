"""Rekam mic (sounddevice) / loopback (soundcard) / keduanya → WAV incremental 16k.
Backend di-import lazy; stop-flag file = sinyal stop lintas proses (CLI & tombol)."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from . import paths, session

SAMPLE_RATE = 16000
BYTES_PER_HOUR_MONO_16BIT = 115 * 1024 * 1024  # ≈ 32 kB/s


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


def _record_mic(job_id: str, wav: Path, mic_device: str | None) -> float:
    """sounddevice blocking stream; return durasi detik. Raise _Stop bila device hilang."""
    import sounddevice as sd

    frames = [0]

    def callback(indata, frame_count, time_info, status):
        f.write(indata.copy())
        f.flush()  # patch header — file selalu valid
        frames[0] += frame_count

    f = _writer(wav)
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
            f.flush()
        except Exception:
            pass
        f.close()
    return frames[0] / SAMPLE_RATE


def _record_sys(job_id: str, wav: Path) -> float:
    """soundcard WASAPI loopback; return durasi detik."""
    import soundcard as sc

    spk = sc.default_speaker()
    mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
    f = _writer(wav)
    n = 0
    try:
        with mic.recorder(samplerate=SAMPLE_RATE, blocksize=8000,
                          channels=1, exclusive_mode=False) as rec:
            for block in rec:
                f.write(np.asarray(block, dtype=np.float32).reshape(-1, 1))
                f.flush()
                n += len(block)
                if session.stop_requested():
                    break
    except (OSError, Exception) as e:  # termasuk device dicabut
        raise _Stop(f"loopback error: {e}") from e
    finally:
        try:
            f.flush()
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
        import sys as _s
        if "soundcard" in _s.modules:
            device = f"{_s.modules['soundcard'].default_speaker().name} loopback"
        wavs = [wav]
    elif source == "both":
        wav_m = tmp / f"{job_id}_mic.wav"
        wav_s = tmp / f"{job_id}_sys.wav"
        errs: list[_Stop] = []

        def run_mic():
            try:
                _record_mic(job_id, wav_m, mic_device)
            except _Stop as e:
                errs.append(e)

        def run_sys():
            try:
                _record_sys(job_id, wav_s)
            except _Stop as e:
                errs.append(e)

        tm = threading.Thread(target=run_mic)
        ts = threading.Thread(target=run_sys)
        tm.start(); ts.start()
        while (tm.is_alive() or ts.is_alive()) and not session.stop_requested():
            time.sleep(0.2)
        if errs:
            lost = True
            session.request_stop()  # satu stream mati → hentikan juga satunya
            tm.join(timeout=5); ts.join(timeout=5)
        else:
            tm.join(); ts.join()
        dur = min(_frames_of(wav_m), _frames_of(wav_s)) / SAMPLE_RATE or 0
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
