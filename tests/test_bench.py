import json

from meeting import bench


def test_bench_writes_measurement_json(meeting_home, tmp_path):
    out = tmp_path / "resources.json"
    result = bench.run(out, workload="finalize", seconds=0.1)
    assert out.exists()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved == result
    assert result["workload"] == "finalize"
    assert result["rss_after_mb"] >= 0
    assert result["cpu_seconds"] >= 0


def test_bench_silence_workload(meeting_home, tmp_path):
    out = tmp_path / "silence.json"
    result = bench.run(out, workload="silence", seconds=0.1)
    assert result["workload"] == "silence"
