import time

from meeting import hwaccel


def test_probe_caches_result(meeting_home, monkeypatch):
    calls = []

    def fake_probe():
        calls.append(1)
        return {"kind": "qsv", "available": True, "opencl_installed": False,
                "devices": ["Intel(R) UHD Graphics"], "checked_at": time.time()}

    monkeypatch.setattr(hwaccel, "_probe", fake_probe)
    first = hwaccel.probe()
    second = hwaccel.probe()
    assert first == second
    assert first["kind"] == "qsv"
    assert len(calls) == 1
    assert hwaccel._cache_path().exists()


def test_probe_respects_off_mode(meeting_home):
    from meeting import config

    config.update(gpu_mode="off")
    result = hwaccel.probe(force=True)
    assert result["available"] is False
    assert result["kind"] == "none"


def test_classify_integrated_gpu(monkeypatch):
    assert hwaccel._classify(["Intel(R) Iris(R) Xe Graphics"]) == "qsv"
    assert hwaccel._classify(["AMD Radeon(TM) Graphics"]) == "vcn"
    assert hwaccel._classify([]) == "none"


def test_probe_does_not_import_gpu_modules(meeting_home, monkeypatch):
    import sys

    before = set(sys.modules)
    monkeypatch.setattr(hwaccel, "_probe", lambda: {
        "kind": "none", "available": False, "opencl_installed": False,
        "devices": [], "checked_at": time.time(),
    })
    hwaccel.probe()
    introduced = {name for name in set(sys.modules) - before
                  if name.startswith(("torch", "cupy", "openvino", "onnxruntime"))}
    assert introduced == set()
