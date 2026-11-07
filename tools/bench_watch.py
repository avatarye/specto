"""What a watched intaker costs on a big folder, and how fast an arrival lands (SPEC-8).

    uv run python tools/bench_watch.py <folder> [--idle 20] [--make 100000] [--keep]

``--make N`` first fills ``<folder>`` with N small files (100 subfolders); ``<folder>``
must then be empty or missing. Otherwise the folder is only read, apart from the one
arrival file the run writes into it and removes again.

A throwaway project whose pinned gallery intaker has the folder as its one source and
``watch: true``, on the real engine and link runtime over a ``ThreadJobManager``
(headless, no Qt). Measured:

- ``baseline``: until the first collection lands (the watcher's baseline listing runs
  meanwhile);
- ``idle cpu``: this process's CPU time over ``--idle`` seconds of nothing arriving,
  as a share of one core, and how many listings the watcher made;
- ``arrival``: one file written into a subfolder, until ``link.arrivals`` and until the
  collection holding it lands;
- ``mode``: how the runtime follows the folder (``notify`` or ``poll``, and why).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import specto_intake  # noqa: F401  (registers the intaker resolver and watcher)
from specto_core import Engine, Project
from specto_core.jobs import ThreadJobManager
from specto_core.links import Collection


def make_folder(folder: Path, n: int) -> None:
    if folder.exists() and any(folder.iterdir()):
        sys.exit(f"[ERROR] --make needs an empty or missing folder: {folder}")
    per = max(1, n // 100)
    for i in range(n):
        d = folder / f"run-{i // per:03d}"
        if i % per == 0:
            d.mkdir(parents=True, exist_ok=True)
        (d / f"{i:06d}_0.txt").write_bytes(b"x")
    print(f"[OK] made {n} files under {folder}")


def pump(engine: Engine, jobs: ThreadJobManager, until, timeout: float) -> float | None:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        jobs.poll()
        engine.links.poll()
        if until():
            return time.perf_counter()
        time.sleep(0.01)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--idle", type=float, default=20.0)
    ap.add_argument("--make", type=int, default=0)
    ap.add_argument("--json", action="store_true", help="print one JSON line at the end")
    args = ap.parse_args()
    folder = args.folder.resolve()
    if args.make:
        make_folder(folder, args.make)
    if not folder.is_dir():
        sys.exit(f"[ERROR] not a folder: {folder}")

    with tempfile.TemporaryDirectory(prefix="specto-bench-watch-") as tmp:
        proj = Project.init(Path(tmp) / "proj", "bench")
        jobs = ThreadJobManager()
        engine = Engine(project=proj, jobs=jobs)
        doc = engine.live.document()
        doc["views"]["gal"] = {
            "type": "gallery",
            "label": "Bench",
            "layout": {"position": [300, 0], "size": [640, 400]},
        }
        doc["links"]["lw"] = {
            "kind": "intaker",
            "scope": "pinned",
            "label": "watched",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "gal", "port": "data"},
            "params": {"sources": [folder.as_posix()], "watch": True},
            "summary": {"sentence": "Everything in the folder."},
        }
        links = engine.links
        arrivals: list[float] = []
        links.events.subscribe(
            lambda ev: arrivals.append(time.perf_counter()) if ev.kind == "link.arrivals" else None
        )
        t0 = time.perf_counter()
        engine.live.submit(doc, origin="agent:bench", label="watch")
        # the submit names new views and links (ADR 0018): find ours by its label
        lid, link = next(
            (k, v) for k, v in engine.live.data["links"].items() if v.get("label") == "watched"
        )
        view = link["target"]["view"]

        def items() -> int:
            col = links.input_of(view, "data")
            return len(col.items) if isinstance(col, Collection) else -1

        t_base = pump(engine, jobs, lambda: items() >= 0, 600)
        if t_base is None:
            sys.exit("[ERROR] the first collection never landed")
        n0 = items()
        print(f"[OK] baseline: {n0} items in {t_base - t0:.2f} s")
        # let the watcher's baseline listing finish before the idle window
        pump(engine, jobs, lambda: False, 3.0)
        st0 = links.watch_status(lid) or {}
        cpu0, wall0 = time.process_time(), time.perf_counter()
        pump(engine, jobs, lambda: False, args.idle)
        cpu1, wall1 = time.process_time(), time.perf_counter()
        st1 = links.watch_status(lid) or {}
        idle_cpu = (cpu1 - cpu0) / (wall1 - wall0)
        listings = int(st1.get("polls", 0)) - int(st0.get("polls", 0))
        print(
            f"[OK] idle cpu: {idle_cpu * 100:.1f} % of a core over {wall1 - wall0:.1f} s, "
            f"{listings} listings"
        )

        sub = sorted(p for p in folder.iterdir() if p.is_dir())
        dest = (sub[len(sub) // 2] if sub else folder) / "zz-bench-arrival.txt"
        arrivals.clear()
        t_write = time.perf_counter()
        dest.write_bytes(b"arrival")
        try:
            t_col = pump(engine, jobs, lambda: items() == n0 + 1, 120)
        finally:
            dest.unlink(missing_ok=True)
        t_arr = arrivals[0] if arrivals else None
        arr_s = f"{t_arr - t_write:.2f} s" if t_arr else "never"
        col_s = f"{t_col - t_write:.2f} s" if t_col else "never"
        print(f"[OK] arrival: link.arrivals after {arr_s}, collection after {col_s}")
        status = links.watch_status(lid) or {}
        print(f"[OK] mode: {status.get('mode', 'poll')} {status.get('reason', '')}".rstrip())
        result = {
            "folder_files": n0,
            "baseline_s": round(t_base - t0, 3),
            "idle_cpu_core_share": round(idle_cpu, 4),
            "idle_listings": listings,
            "arrival_event_s": round(t_arr - t_write, 3) if t_arr else None,
            "arrival_collection_s": round(t_col - t_write, 3) if t_col else None,
            "status": status,
        }
        engine.close()
        jobs.shutdown()
    if args.json:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.exit(main())
