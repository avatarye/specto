"""Benchmark: thumbnail a directory of images through the worker pool.

    python tools/bench_thumbnails.py [--dir DIR] [--size 192] [--workers N] [--batch 16]
                                      [--limit N] [--inline]

Reports images/s for the pool (one job per image, and batched), and single-process
inline decoding for reference. Plain ASCII output.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

#: a folder of images, read only: the test data's cam-a frames (``SPECTO_TESTDATA`` or
#: the repo's ``testdata/``) unless ``--dir`` names another, larger one
TESTDATA = Path(os.environ.get("SPECTO_TESTDATA", Path(__file__).resolve().parents[1] / "testdata"))
DEFAULT_DIR = str(TESTDATA / "runs" / "run-001" / "cam-a")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--size", type=int, default=192)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--inline", action="store_true", help="also time single-process decoding")
    args = ap.parse_args()

    from specto_core.kinds import DataRef, DecodeRequest
    from specto_readers import (
        PoolJobManager,
        default_readers,
        release_result,
        submit_decode,
        submit_decode_batch,
    )

    paths = sorted(
        p for p in Path(args.dir).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        print(f"[ERROR] no images under {args.dir}")
        return 1
    refs = [DataRef(kind="image2d", uri=str(p)) for p in paths]
    req = DecodeRequest(purpose="thumbnail", max_size=args.size)
    n = len(refs)
    print(f"[OK] {n} images under {args.dir}; thumbnail max_size={args.size}")

    if args.inline:
        reg = default_readers()
        t0 = time.perf_counter()
        for r in refs:
            reg.decode(r, req)
        dt = time.perf_counter() - t0
        print(f"[OK] inline (1 process): {n / dt:8.1f} img/s  ({dt:.2f} s)")

    workers = args.workers or max(1, (os.cpu_count() or 4) - 2)
    with PoolJobManager(workers=workers) as pool:
        t0 = time.perf_counter()
        warm = [submit_decode(pool, r, req) for r in refs[:workers]]
        pool.wait(warm, 120)
        spawn_s = time.perf_counter() - t0
        for j in warm:
            release_result(j)
        print(f"[OK] pool: {workers} workers, spawn + first decode {spawn_s:.2f} s")

        # one job per image
        done = 0
        bytes_total = 0

        def on_done(job) -> None:
            nonlocal done, bytes_total
            done += 1
            if job.state == "done":
                bytes_total += job.result.spec.nbytes
                release_result(job)
            else:
                print(f"[WARN] {job.id} {job.state}: {(job.error or '').splitlines()[0]}")

        t0 = time.perf_counter()
        jobs = [submit_decode(pool, r, req, on_done=on_done) for r in refs]
        pool.wait(jobs, 600)
        dt = time.perf_counter() - t0
        failed = sum(1 for j in jobs if j.state != "done")
        print(
            f"[OK] pool, 1 job/image: {n / dt:8.1f} img/s  ({dt:.2f} s, {failed} failed, "
            f"{bytes_total / 1e6:.1f} MB RGBA through shm)"
        )

        # batched
        t0 = time.perf_counter()
        batches = [refs[i : i + args.batch] for i in range(0, n, args.batch)]
        jobs = [submit_decode_batch(pool, b, req) for b in batches]
        pool.wait(jobs, 600)
        dt = time.perf_counter() - t0
        bad = 0
        for j in jobs:
            if j.state != "done":
                bad += len(batches[jobs.index(j)])
                continue
            bad += sum(1 for x in j.result if isinstance(x, dict))
            release_result(j)
        print(
            f"[OK] pool, batches of {args.batch}: {n / dt:8.1f} img/s  ({dt:.2f} s, {bad} failed)"
        )
        print(f"[OK] respawns: {pool.respawns}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
