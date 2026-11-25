"""Benchmark ukuran/durasi codec audio (FLAC vs Opus). Hasil ditulis ke file JSON,
bukan stdout — dipakai script/CI. ffmpeg opsional; tanpa itu kandidat Opus ditandai
tidak tersedia."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 16000


def synth_speech(seconds: float = 8.0, sr: int = SR, seed: int = 7) -> np.ndarray:
    """Sinyal speech-like deterministik: suku kata bersuara + jeda + noise rendah."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n, dtype=np.float32) / sr
    out = np.zeros(n, dtype=np.float32)
    for start in np.arange(0.0, seconds, 0.25):
        i0, i1 = int(start * sr), min(n, int((start + 0.18) * sr))
        if i0 >= n or i1 <= i0:
            break
        f0 = 110.0 + 40.0 * rng.random()
        env = np.sin(np.pi * np.linspace(0, 1, i1 - i0, dtype=np.float32)) ** 2
        voiced = (
            0.45 * np.sin(2 * np.pi * f0 * t[i0:i1])
            + 0.18 * np.sin(2 * np.pi * 2 * f0 * t[i0:i1])
            + 0.06 * rng.standard_normal(i1 - i0).astype(np.float32)
        )
        out[i0:i1] += env * voiced
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.6).astype(np.float32)


def _duration(path: Path) -> float | None:
    try:
        info = sf.info(str(path))
        return round(info.frames / info.samplerate, 3)
    except Exception:
        pass
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        )
        return round(float(r.stdout.strip()), 3) if r.returncode == 0 else None
    except Exception:
        return None


def _measure(path: Path) -> dict:
    return {
        "size_bytes": path.stat().st_size,
        "duration_sec": _duration(path),
        "available": True,
    }


def _encode_opus(src: Path, dst: Path, bitrate: str) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    argv = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-c:a", "libopus", "-vbr", "on", "-application", "voip",
        "-b:a", bitrate, "-ac", "1", str(dst),
    ]
    try:
        r = subprocess.run(argv, capture_output=True, timeout=120)
    except Exception:
        return False
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def bench(out_path: Path, *, seconds: float = 8.0, sr: int = SR) -> dict:
    """Jalankan benchmark, tulis JSON ke `out_path`, kembalikan dict yang sama."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        wav = td_path / "speech.wav"
        data = synth_speech(seconds, sr)
        sf.write(str(wav), data, sr, subtype="PCM_16")

        flac = td_path / "speech.flac"
        t0 = time.monotonic()
        sf.write(str(flac), data, sr, format="FLAC")
        flac_sec = round(time.monotonic() - t0, 3)

        reports: dict = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "input": {"seconds": seconds, "samplerate": sr,
                      "size_bytes": wav.stat().st_size},
            "codecs": {"flac": {**_measure(flac), "encode_sec": flac_sec}},
        }
        for name, bitrate in (("opus_balanced", "32k"), ("opus_best", "64k")):
            dst = td_path / f"speech_{name}.opus"
            t0 = time.monotonic()
            ok = _encode_opus(wav, dst, bitrate)
            if ok:
                reports["codecs"][name] = {
                    **_measure(dst), "encode_sec": round(time.monotonic() - t0, 3),
                }
            else:
                reports["codecs"][name] = {
                    "available": False,
                    "reason": "ffmpeg/libopus tidak tersedia",
                }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    return reports


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Benchmark codec audio meeting-transcriber")
    p.add_argument("--out", default="data/bench/codecs.json")
    p.add_argument("--seconds", type=float, default=8.0)
    args = p.parse_args(argv)
    bench(Path(args.out), seconds=args.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
