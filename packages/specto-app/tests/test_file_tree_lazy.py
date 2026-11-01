"""Seam 2 (#231): the file tree lists folders on demand and starts collapsed. Opening a
project shows the top level without a recursive scan; a chain of single-child folders
opens on its own; expanding a folder lists that folder alone, on the tree's worker
thread; a folder pick still stands for every file under it; restored picks, the
``selection`` input and ``params.selected`` from outside load the folders they need and
reveal; auto-update follows the loaded folders only; the GUI thread never waits on the
disk. Headless hosts over the real engine and link runtime."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.hosts import HostContext
from specto_app.views.file_tree import FileTreeHost
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager, ThreadJobManager
from specto_core.links import FilesValue, Selection
from specto_intake import listing, resolve, sources

SHIFT = int(Qt.KeyboardModifier.ShiftModifier.value)
FILES = {
    "a/f1.txt": b"1",
    "a/f2.txt": b"22",
    "a/deep/f3.txt": b"333",
    "b/sub/x.txt": b"x",
    "b/sub/y.txt": b"yy",
    "b/z.txt": b"z",
    "top.md": b"#",
}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "lazy")
    for rel, data in FILES.items():
        p = proj.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return proj


@pytest.fixture
def no_walk(monkeypatch: pytest.MonkeyPatch):
    """The recursive scan is never called for the tree."""

    def walk(*a, **k):
        raise AssertionError("walk_source called")

    monkeypatch.setattr(sources, "walk_source", walk)
    monkeypatch.setattr(resolve, "walk_source", walk)


def host_on(engine: Engine) -> tuple[HostContext, FileTreeHost]:
    ctx = HostContext(engine, None)
    host = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
    ctx.links.attach("fege", host.receive)
    return ctx, host


def rows_of(host: FileTreeHost) -> list[tuple[str, bool]]:
    return [(r["path"], r["expanded"]) for r in host.rows()]


def picks(ctx: HostContext) -> FilesValue:
    return ctx.links.output_of("fege", "selected_files")


# -- opening ------------------------------------------------------------------------------
def test_opening_a_project_lists_the_top_level_only_collapsed_and_without_a_walk(
    qapp, spin, project: Project, no_walk
):
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs)
    ctx, host = host_on(engine)
    spin(until=lambda: host.rows() != [])

    assert rows_of(host) == [("a", False), ("b", False), ("top.md", False)]
    assert host.worker.last_thread not in (None, threading.get_ident())  # off the GUI thread
    snap = host.snapshot()
    assert snap["complete"] is False and snap["loaded"] == [""] and snap["expanded"] == []
    assert ctx.health_of("fege") == {"status": "ok", "message": "3 rows listed", "hint": ""}
    assert picks(ctx) == FilesValue(root=str(project.root), paths=[], stats={})
    assert ctx.links.project_files is None
    host.dispose()
    jobs.shutdown()


def test_a_chain_of_single_child_folders_opens_on_its_own_and_nothing_else(tmp_path: Path, no_walk):
    proj = Project.init(tmp_path / "chain", "chain")
    for rel in ("vault/notes/d000/n.md", "vault/notes/d001/n.md", "vault/notes/readme.md"):
        (proj.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (proj.root / rel).write_bytes(b"x")
    engine = Engine(project=proj, jobs=InlineJobManager())
    _ctx, host = host_on(engine)

    assert rows_of(host) == [
        ("vault", True),
        ("vault/notes", True),
        ("vault/notes/d000", False),
        ("vault/notes/d001", False),
        ("vault/notes/readme.md", False),
    ]
    assert host.snapshot()["loaded"] == ["", "vault", "vault/notes"]


def test_expanding_a_folder_lists_that_folder_alone_and_counts_once_its_subtree_is_known(
    project: Project, no_walk, monkeypatch: pytest.MonkeyPatch
):
    listed: list[str] = []
    real = listing.TreeLister.list_dir

    def list_dir(self, path=""):
        if str(self.base) == str(project.root):  # this tree's listings, not a leftover host's
            listed.append(path)
        return real(self, path)

    monkeypatch.setattr(listing.TreeLister, "list_dir", list_dir)
    engine = Engine(project=project, jobs=InlineJobManager())
    _ctx, host = host_on(engine)
    assert listed == [""]

    host.toggle(1)  # b
    assert listed == ["", "b"]
    assert rows_of(host) == [
        ("a", False),
        ("b", True),
        ("b/sub", False),
        ("b/z.txt", False),
        ("top.md", False),
    ]
    counts = {
        r["path"]: host.model.data(host.model.index(i), host.model.CountRole)
        for i, r in enumerate(host.rows())
    }
    assert counts["b"] == 0 and counts["a"] == 0  # not known yet: no count shown
    host.toggle(2)  # b/sub
    host.toggle(2)  # and closed again: it stays loaded
    assert listed == ["", "b", "b/sub"]
    counts = {
        r["path"]: host.model.data(host.model.index(i), host.model.CountRole)
        for i, r in enumerate(host.rows())
    }
    assert counts["b/sub"] == 2
    host.toggle(1)  # b closed: three files under it, every folder below listed
    assert host.model.data(host.model.index(1), host.model.CountRole) == 3
    assert host.model.node("b").size == 4  # x + yy + z
    host.toggle(1)  # opening again lists nothing anew
    assert listed == ["", "b", "b/sub"]


# -- picks --------------------------------------------------------------------------------
def test_a_folder_pick_stands_for_every_file_under_it_resolved_on_the_worker(
    qapp, spin, project: Project, no_walk
):
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs)
    ctx, host = host_on(engine)
    spin(until=lambda: host.rows() != [])
    main = threading.get_ident()

    host.click(0)  # a, not listed yet
    spin(until=lambda: picks(ctx).paths != [])
    value = picks(ctx)
    assert value.paths == ["a/deep/f3.txt", "a/f1.txt", "a/f2.txt"]
    assert value.stats["a/f2.txt"][0] == 2 and set(value.stats) == set(value.paths)
    assert host.worker.last_thread != main
    assert host.snapshot()["selected"] == value.paths
    assert engine.live.data["views"]["fege"]["params"]["selected"] == ["a"]

    host.click(1, SHIFT)  # a shift range over two unlisted folders
    spin(until=lambda: len(picks(ctx).paths) == 6)
    assert picks(ctx).paths == sorted(p for p in FILES if p != "top.md")

    host.click(2)  # a file: known, goes out at once
    assert picks(ctx).paths == ["top.md"] and picks(ctx).stats == {
        "top.md": [1, (project.root / "top.md").stat().st_mtime_ns]
    }
    host.dispose()
    jobs.shutdown()


def test_only_the_latest_folder_pick_goes_out(
    qapp, spin, project: Project, no_walk, monkeypatch: pytest.MonkeyPatch
):
    real = listing.TreeLister.files_under

    def slow(self, paths, cancelled=None):
        time.sleep(0.15)
        return real(self, paths, cancelled)

    monkeypatch.setattr(listing.TreeLister, "files_under", slow)
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs)
    ctx, host = host_on(engine)
    spin(until=lambda: host.rows() != [])
    landed: list[list[str]] = []
    engine.live.events.subscribe(
        lambda ev: landed.append(list(picks(ctx).paths)) if ev.kind == "link.resolving" else None
    )

    host.click(0)  # a
    host.click(1)  # b, before a's files came back
    spin(until=lambda: picks(ctx).paths != [], timeout=5)
    spin(200)
    assert picks(ctx).paths == ["b/sub/x.txt", "b/sub/y.txt", "b/z.txt"]
    assert not any("a/f1.txt" in p for p in landed)  # a's answer never went out
    host.dispose()
    jobs.shutdown()


# -- revealing ----------------------------------------------------------------------------
def test_restored_picks_the_selection_input_and_params_selected_load_their_folders(
    qapp, spin, project: Project, no_walk
):
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs)
    engine.live.remember("fege", {"selected": ["b/sub/x.txt"]})  # as a reopened project
    ctx, host = host_on(engine)
    spin(until=lambda: picks(ctx) is not None and picks(ctx).paths == ["b/sub/x.txt"])
    assert host.model.selected == {"b/sub/x.txt"}
    assert {"b", "b/sub"} <= host.model.expanded
    assert host.snapshot()["loaded"] == ["", "b", "b/sub"]  # a was not listed for it

    host.receive("selection", Selection(keys=["a/deep/f3.txt"], last="a/deep/f3.txt"))
    spin(until=lambda: picks(ctx).paths == ["a/deep/f3.txt"])
    assert host.model.selected == {"a/deep/f3.txt"} and {"a", "a/deep"} <= host.model.expanded
    assert engine.live.data["views"]["fege"]["params"]["selected"] == ["a/deep/f3.txt"]

    # the agent's canvas.open_file: params.selected written from outside
    engine.live.remember("fege", {"selected": ["b/z.txt"]})
    host.on_patch([{"op": "replace", "path": "/views/fege/params/selected"}], {})
    spin(until=lambda: picks(ctx).paths == ["b/z.txt"])
    assert host.model.selected == {"b/z.txt"}

    # a key the tree does not hold waits for its data; the user's own pick drops it
    host.receive("selection", Selection(keys=["b/sub/nope.txt"], last="b/sub/nope.txt"))
    spin(100)
    assert host._wanted == ["b/sub/nope.txt"] and host.model.selected == set()
    host.click(host.model.row_of("top.md"))
    assert host._wanted is None and picks(ctx).paths == ["top.md"]
    host.dispose()
    jobs.shutdown()


def test_a_double_click_on_a_file_in_a_folder_listed_on_expand_opens_it(project: Project, no_walk):
    engine = Engine(project=project, jobs=InlineJobManager())
    _ctx, host = host_on(engine)
    host.toggle(1)  # b
    opened = host.open_row(host.model.row_of("b/z.txt"))
    assert opened is not None and opened.action == "created"
    assert engine.live.data["views"]["fege"]["params"]["selected"] == ["b/z.txt"]
    assert host.open_row(host.model.row_of("b/sub")) is None  # a folder is not opened


# -- auto-update ----------------------------------------------------------------------------
def test_auto_update_lists_the_loaded_folders_only_and_shows_arrivals_in_place(
    qapp, spin, project: Project, no_walk, monkeypatch: pytest.MonkeyPatch
):
    listed: list[str] = []
    real = listing.TreeLister.list_dir

    def list_dir(self, path=""):
        if str(self.base) == str(project.root):  # this tree's listings, not a leftover host's
            listed.append(path)
        return real(self, path)

    monkeypatch.setattr(listing.TreeLister, "list_dir", list_dir)
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs, watch_interval_s=0.05)
    ctx, host = host_on(engine)
    spin(until=lambda: host.rows() != [])
    assert (
        host._watch.isActive() and ctx.links.watching() == []
    )  # the tree's own, not the runtime's
    host.toggle(1)  # b
    spin(until=lambda: host.model.node("b").loaded)
    host.click(host.model.row_of("b/z.txt"))
    before = picks(ctx)

    (project.root / "b" / "late.txt").write_bytes(b"late")
    (project.root / "a" / "unseen.txt").write_bytes(b"?")
    spin(until=lambda: host.model.node("b/late.txt") is not None, timeout=5)
    spin(150)
    assert "b/sub" not in listed and "a" not in listed  # only the loaded folders
    assert host.model.node("a/unseen.txt") is None
    assert picks(ctx) is before  # the pick's files did not move: nothing went out again
    assert "b/late.txt" in [r["path"] for r in host.rows()]

    # the switch off: the tree stops listing
    from specto_core import gestures

    gestures.set_auto_update(engine.live, "fege", False)
    spin(until=lambda: not host._watch.isActive())
    n = len(listed)
    spin(150)
    assert len(listed) == n
    # the refresh (the project node's, the view's): the loaded folders once more
    ctx.links.refresh()
    spin(until=lambda: len(listed) > n)
    assert set(listed[n:]) <= {"", "b"}
    host.dispose()
    jobs.shutdown()


# -- the GUI thread -------------------------------------------------------------------------
def test_the_gui_thread_never_waits_on_a_listing(
    qapp, spin, project: Project, no_walk, monkeypatch: pytest.MonkeyPatch
):
    real = listing.TreeLister.list_dir

    def slow(self, path=""):
        time.sleep(0.3)
        return real(self, path)

    monkeypatch.setattr(listing.TreeLister, "list_dir", slow)
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs)
    ctx = HostContext(engine, None)
    host = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
    t0 = time.perf_counter()
    host.receive("data", ctx.links.input_of("fege", "data"))
    host.toggle(0)
    assert time.perf_counter() - t0 < 0.1  # nothing listed on this thread
    assert host.rows() == []
    spin(until=lambda: host.rows() != [], timeout=5)
    host.dispose()
    jobs.shutdown()


def test_a_tree_fed_explicit_files_shows_them_whole_as_before(tmp_path: Path) -> None:
    """A collection of explicit files (another tree's picks): every folder is loaded at
    once, with the stat the intaker recorded."""
    from test_ports_hosts import tree_collection

    engine = Engine(project=None, jobs=InlineJobManager())
    ctx = HostContext(engine, None)
    host = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
    host.receive("data", tree_collection(tmp_path / "data", ["x/1.txt", "x/2.txt", "y.txt"]))
    assert host.model.complete and host.snapshot()["files"] == 3
    assert rows_of(host) == [("x", False), ("y.txt", False)]
    host.toggle(0)
    assert host.model.data(host.model.index(0), host.model.CountRole) == 2
    assert host.lister is None
