"""Seam 1 (#70): an intaker fed an explicit path list (what a file tree emits on
``all_files`` and ``selected_files``) does work proportional to the files it keeps, not
to the list it is handed. A vault of 16k files made a pinned one-note intaker take 5 s
per re-resolve and a folder pick 8 s: every candidate path was resolved three times and
stat'ed twice before the source match."""

from __future__ import annotations

import os
import pathlib
import time
from pathlib import Path

import pytest
from intake_helpers import ALIASES

import specto_intake  # noqa: F401
from specto_core.links import FilesValue
from specto_intake.resolve import resolve_intaker

ENV = {"aliases": ALIASES, "ext_kinds": {}}


@pytest.fixture
def counters(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """How often the resolver stats a path and resolves one (``Path.resolve`` opens the
    file on Windows: the expensive call on a synced folder)."""
    n = {"stat": 0, "resolve": 0}
    real_stat, real_resolve = os.stat, pathlib.Path.resolve

    def stat(*a, **k):
        n["stat"] += 1
        return real_stat(*a, **k)

    def resolve(self, *a, **k):
        n["resolve"] += 1
        return real_resolve(self, *a, **k)

    monkeypatch.setattr(os, "stat", stat)
    monkeypatch.setattr(pathlib.Path, "resolve", resolve)
    return n


def intaker(sources: list[str], screener: list[str] = ()) -> dict:
    return {
        "kind": "intaker",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": sources, "screener": list(screener)},
    }


def test_a_pinned_intaker_touches_only_its_own_file_when_the_tree_emits_every_path(
    tmp_path: Path, counters: dict[str, int]
):
    note = tmp_path / "notes" / "findings.md"
    note.parent.mkdir()
    note.write_text("# findings\n", encoding="utf-8")
    # the file tree's all_files: the whole vault, of which one path is this intaker's
    paths = [f"data/run{i // 100:03d}/frame{i:05d}.jpg" for i in range(20_000)]
    paths.insert(7_000, "notes/findings.md")
    link = intaker(["notes/findings.md"])
    t0, c0 = time.perf_counter(), time.thread_time()
    value = FilesValue(root=str(tmp_path), paths=paths)
    col = resolve_intaker(link, value, root=str(tmp_path), env=ENV)
    elapsed, cpu = time.perf_counter() - t0, time.thread_time() - c0
    assert [it.key for it in col.items] == ["findings"]
    assert counters["stat"] <= 4, f"{counters['stat']} stats for one kept file"
    assert counters["resolve"] <= 4, f"{counters['resolve']} resolves for one kept file"
    # the work itself, not the wall clock: parallel suite copies preempt this thread (#179)
    assert cpu < 2.0, f"{cpu:.2f}s of CPU ({elapsed:.2f}s wall) to place 20k paths"


def test_a_folder_pick_stats_each_kept_file_once_and_resolves_no_path_per_file(
    tmp_path: Path, counters: dict[str, int]
):
    files = []
    for i in range(1_500):
        p = tmp_path / "data" / f"run{i // 100:03d}" / f"frame{i:05d}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        files.append(p.relative_to(tmp_path).as_posix())
    counters.update(stat=0, resolve=0)  # the setup's own calls
    link = intaker(["."], ["@images"])
    value = FilesValue(root=str(tmp_path), paths=files)
    col = resolve_intaker(link, value, root=str(tmp_path), env=ENV)
    assert len(col.items) == 1_500
    assert counters["stat"] <= 1_500 + 10, f"{counters['stat']} stats for 1500 kept files"
    assert counters["resolve"] <= 10, f"{counters['resolve']} resolves for 1500 kept files"
