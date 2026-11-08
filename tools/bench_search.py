"""How fast the file tree's type-to-search answers through Everything (#232, SPEC-89).

    uv run python tools/bench_search.py [<folder>] [--text readme] [--runs 20] [--bound-ms 100]

Runs ``specto_intake.search.search`` ``--runs`` times under ``<folder>`` (default: the
test data, ``SPECTO_TESTDATA``) with Everything as the backend, and reports the wall
time per search (min, median, max), Everything's own ``elapsed_ms`` and this thread's
CPU time. Exits 1 when the median is over ``--bound-ms``, 2 when Everything is not
running or the folder is not indexed.

The latency promise lives here, not in the test suite: nearly all of a search's time
is the wait for Everything's reply over the SDK's IPC (this thread's CPU time is under
the 15.6 ms timer tick), so a wall-clock bound in a full run measures how busy the
machine is. Run it on a quiet machine.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

from specto_intake import search as fs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default = os.environ.get("SPECTO_TESTDATA") or str(
        Path(__file__).resolve().parents[1] / "testdata"
    )
    ap.add_argument("folder", nargs="?", default=default)
    ap.add_argument("--text", default="readme")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--bound-ms", type=float, default=100.0)
    args = ap.parse_args()

    folder = str(Path(args.folder))
    everything = fs.EverythingSearch()
    if not everything.available(folder):
        print(f"[ERROR] Everything is not running, or {folder} is not indexed")
        return 2

    walls: list[float] = []
    cpus: list[float] = []
    reported: list[float] = []
    for _ in range(max(1, args.runs)):
        w0, c0 = time.perf_counter(), time.thread_time()
        result = fs.search(folder, args.text, 100, everything=everything)
        walls.append((time.perf_counter() - w0) * 1000)
        cpus.append((time.thread_time() - c0) * 1000)
        reported.append(result.elapsed_ms)
        if result.backend != "everything":
            print(f"[ERROR] the {result.backend} backend answered, not Everything")
            return 2

    median = statistics.median(walls)
    print(f"folder    {folder}")
    print(f"query     {args.text!r}, {len(result.hits)} hits, {args.runs} runs")
    print(f"wall      min {min(walls):.1f} ms, median {median:.1f} ms, max {max(walls):.1f} ms")
    print(f"reported  median {statistics.median(reported):.1f} ms (Everything's elapsed_ms)")
    print(f"cpu       median {statistics.median(cpus):.1f} ms (this thread)")
    if median > args.bound_ms:
        print(f"[ERROR] median {median:.1f} ms is over the {args.bound_ms:.0f} ms bound")
        return 1
    print(f"[OK] median under {args.bound_ms:.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
