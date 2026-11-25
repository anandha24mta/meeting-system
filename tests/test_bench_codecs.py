import importlib.util
from pathlib import Path

import numpy as np

_BENCH_FILE = Path(__file__).resolve().parents[1] / "tools" / "bench_codecs.py"
_spec = importlib.util.spec_from_file_location("bench_codecs", _BENCH_FILE)
bench_codecs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench_codecs)


def test_synth_speech_is_deterministic_and_bounded():
    a = bench_codecs.synth_speech(0.5)
    b = bench_codecs.synth_speech(0.5)
    assert a.shape == (8000,)
    assert np.array_equal(a, b)
    assert float(np.max(np.abs(a))) <= 0.6 + 1e-6


def test_bench_writes_report_to_file_not_stdout(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(bench_codecs.shutil, "which", lambda name: None)
    out = tmp_path / "sub" / "codecs.json"
    report = bench_codecs.bench(out, seconds=0.3)
    assert out.exists()
    assert report["codecs"]["flac"]["available"] is True
    assert report["codecs"]["flac"]["size_bytes"] > 0
    assert report["codecs"]["opus_balanced"]["available"] is False
    assert capsys.readouterr().out == ""
