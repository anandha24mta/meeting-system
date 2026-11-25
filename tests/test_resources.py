import pytest

from meeting import config, queuer, resources


def test_plan_caps_by_cpu_ram_and_config(meeting_home):
    cfg = {"max_workers": 8, "max_parallel_chunks": 3, "per_job_memory_mb": 256, "thread_cap": 1}
    info = resources.plan(cfg, cpu=16, ram_mb=64 * 1024)
    assert info["max_workers"] == 8
    assert info["max_parallel_chunks"] == 3
    assert info["thread_cap"] == 1

    info = resources.plan(cfg, cpu=4, ram_mb=512)
    assert info["max_workers"] == 2  # cpu // 2 dan RAM // 256

    info = resources.plan(cfg, cpu=1, ram_mb=128)
    assert info["max_workers"] == 1


def test_plan_uses_config_defaults(meeting_home):
    info = resources.plan(config.load(), cpu=16, ram_mb=64 * 1024)
    assert info["max_workers"] == 4
    assert info["max_parallel_chunks"] == 2
    assert info["thread_cap"] == 2


def test_apply_thread_limits(meeting_home, monkeypatch):
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.delenv("MKL_NUM_THREADS", raising=False)
    resources.apply_thread_limits({"thread_cap": 3})
    assert __import__("os").environ["OMP_NUM_THREADS"] == "3"
    assert __import__("os").environ["MKL_NUM_THREADS"] == "3"


def test_spawn_uses_planner_cap(meeting_home, monkeypatch):
    from meeting import paths

    paths.ensure_dirs()
    for i in range(10):
        queuer.enqueue({"id": f"j{i}", "status": "pending"})
    monkeypatch.setattr(resources, "cpu_count", lambda: 6)
    monkeypatch.setattr(resources, "ram_total_mb", lambda: 4096.0)
    config.update(max_workers=9)

    calls = []

    class FakePopen:
        def __init__(self, *a, **kw):
            calls.append(a)

    monkeypatch.setattr(queuer.subprocess, "Popen", FakePopen)
    queuer.spawn_worker()
    assert len(calls) == 3  # min(9, 6//2, 4096//256)
