"""CLI wrapper: python tools/bench_resources.py --out data/bench/resources.json"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meeting import bench  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Benchmark local meeting-system steps")
    p.add_argument("--out", default="data/bench/resources.json")
    p.add_argument("--workload", choices=("finalize", "silence"), default="finalize")
    p.add_argument("--seconds", type=float, default=60.0)
    args = p.parse_args()
    result = bench.run(Path(args.out), args.workload, args.seconds)
    print(f"{result['workload']}: {result['rss_after_mb']} MB RSS, {result['seconds']} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
