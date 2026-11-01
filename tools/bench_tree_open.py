"""How long until a file tree on a big folder shows rows (#231). Read-only.

    uv run python tools/bench_tree_open.py <folder> [<folder> ...] [--repeat 3]

For each folder: a throwaway project whose file tree intaker has the folder as its one
source (nothing is written into the folder), the real engine, link runtime and
``FileTreeHost`` headless over a ``ThreadJobManager`` (the tree's own worker thread
lists; results land through the Qt loop, as in the app). Measured from the host
receiving its data:

- ``first rows``: the first ``rowsInserted`` of the model (the top level, or the first
  step of the chain down to a folder outside the project);
- ``folder listed``: the folder's own children are on screen (the end of the single-child
  chain when the source lies outside the project);
- ``root pick``: a pick of the folder itself resolved to every file under it on the
  worker (``TreeLister.files_under``: the recursive walk a pick still costs);
- ``list_dir``: one listing of the folder's top level, on this thread, for reference.

It asserts that ``walk_source`` is never called while the tree loads.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# isort: skip_file
# (pydantic and the specto packages must load before PySide6's import hook)
import pydantic  # noqa: F401

import specto_intake  # noqa: F401
from specto_core import Engine, Project
from specto_core.jobs import ThreadJobManager
from specto_intake import resolve, sources
from specto_intake.listing import TreeLister
from PySide6.QtCore import QCoreApplication
from specto_app.hosts import HostContext
from specto_app.views.file_tree import FileTreeHost


def no_walk(*a, **k):
    raise AssertionError("walk_source was called while the tree loaded")


def spin(app: QCoreApplication, jobs: ThreadJobManager, until, timeout: float = 120.0) -> float:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        app.processEvents()
        jobs.poll()
        if until():
            return time.perf_counter()
        time.sleep(0.001)
    raise TimeoutError("timed out")


def bench(folder: str, app: QCoreApplication) -> dict:
    folder = os.path.abspath(folder)
    with tempfile.TemporaryDirectory(prefix="specto-bench-tree-") as tmp:
        project = Project.init(Path(tmp) / "proj", "bench")
        doc = json.loads(project.canvas_file.read_text(encoding="utf-8"))
        (lid,) = [k for k, ln in doc["links"].items() if ln["source"]["view"] == "project"]
        doc["links"][lid]["params"]["sources"] = [folder.replace("\\", "/")]
        doc["links"][lid]["params"]["watch"] = False
        project.canvas_file.write_text(json.dumps(doc), encoding="utf-8")
        project = Project.open(project.root)

        jobs = ThreadJobManager()
        engine = Engine(project=project, jobs=jobs)
        ctx = HostContext(engine, None)
        host = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
        stamps: dict[str, float] = {}
        host.model.rowsInserted.connect(
            lambda *a: stamps.setdefault("first_rows", time.perf_counter())
        )
        real_sources, real_resolve = sources.walk_source, resolve.walk_source
        sources.walk_source = resolve.walk_source = no_walk
        try:
            t0 = time.perf_counter()
            ctx.links.attach("fege", host.receive)
            spin(app, jobs, lambda: "first_rows" in stamps)
            # the folder's own children: the single-child chain down to it is over
            chain = folder.replace("\\", "/")
            t_listed = spin(
                app,
                jobs,
                lambda: (
                    host._chain is None
                    and host.model.node(chain) is not None
                    and host.model.node(chain).loaded
                ),
            )
            top = host.model.node(chain)
            n_top = len(top.children) if top else 0
            t1 = time.perf_counter()
            host.select_paths([chain])
            t_pick = spin(app, jobs, lambda: host.snapshot()["selected"] != [], timeout=600)
            n_files = len(host.snapshot()["selected"])
        finally:
            sources.walk_source, resolve.walk_source = real_sources, real_resolve
            host.dispose()
            engine.close()
            jobs.shutdown()
        t2 = time.perf_counter()
        entries = TreeLister(project.root, {"sources": [chain]}).list_dir(chain)
        t_list = time.perf_counter() - t2
        return {
            "folder": folder,
            "first_rows_ms": round((stamps["first_rows"] - t0) * 1000, 1),
            "folder_listed_ms": round((t_listed - t0) * 1000, 1),
            "top_level_entries": n_top,
            "root_pick_s": round(t_pick - t1, 2),
            "files_under_root": n_files,
            "list_dir_ms": round(t_list * 1000, 2),
            "list_dir_entries": len(entries),
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()
    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    for folder in args.folders:
        print(f"\n== {folder}", flush=True)
        for i in range(args.repeat):
            r = bench(folder, app)
            print(
                f"  run {i + 1}: first rows {r['first_rows_ms']} ms; folder listed "
                f"{r['folder_listed_ms']} ms ({r['top_level_entries']} entries); "
                f"root pick {r['root_pick_s']} s ({r['files_under_root']} files); "
                f"list_dir {r['list_dir_ms']} ms",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
