"""Seam 2 (#232): the file tree's type-to-search. Only the latest keystroke's hits show
and the query in flight is cancelled; hits pass the tree's screener and the note says
when there are more; a hit behaves like a row (click picks, double-click opens, reveal
returns to the tree at it); the scan answers when Everything is unavailable and a miss
names the backend; in the real shell, typing in the focused tree starts a search and
Escape returns to the tree. Headless hosts and the offscreen shell over the real engine."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.hosts import HostContext
from specto_app.views.file_tree import FileTreeHost
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager, ThreadJobManager
from specto_core.links import FilesValue
from specto_intake import search as fs
from specto_intake.everything import Hit

pytest.importorskip("PySide6.QtQuick")

from specto_app.app import find_item

CTRL = int(Qt.KeyboardModifier.ControlModifier.value)
FILES = {
    "a/f1.txt": b"1",
    "a/f2.txt": b"22",
    "a/deep/f3.txt": b"333",
    "a/deep/f3.jpg": b"jpg",
    "b/sub/x.txt": b"x",
    "b/z.txt": b"z",
    "top.md": b"#",
}


class Index:
    """A stand-in Everything: up or down, answering ``hits`` under any root."""

    def __init__(self, hits: list[str] | None = None, *, up: bool = True, delay: float = 0.0):
        self.up = up
        self.hits = hits or []
        self.delay = delay
        self.queries: list[str] = []
        self.cancelled: list[bool] = []

    def available(self) -> bool:
        return self.up

    def search(self, query: str, *, limit: int, cancelled=None, sort=0):
        self.queries.append(query)
        base = query.split('"')[1]
        if self.delay:
            t_end = time.monotonic() + self.delay
            while time.monotonic() < t_end:
                time.sleep(0.01)
                if cancelled is not None and cancelled():
                    break
            self.cancelled.append(bool(cancelled and cancelled()))
        if query.endswith("file:"):  # the probe: is the root indexed at all?
            return [], len(self.hits)
        word = query.rsplit('"', 2)[-2].lower()
        out = [Hit(base + p.replace("/", "\\"), 7, 1) for p in self.hits if word in p.lower()]
        return out[:limit], len(out)


def everything(index: Index) -> fs.EverythingSearch:
    return fs.EverythingSearch(index, probe_ttl_s=0)  # type: ignore[arg-type]


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "search")
    for rel, data in FILES.items():
        p = proj.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return proj


def host_on(engine: Engine) -> tuple[HostContext, FileTreeHost]:
    ctx = HostContext(engine, None)
    host = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
    ctx.links.attach("fege", host.receive)
    return ctx, host


def hits_of(host: FileTreeHost) -> list[str]:
    return [h.path for h in host.search.model.hits]


def picks(ctx: HostContext) -> FilesValue:
    return ctx.links.output_of("fege", "selected_files")


# -- the latest keystroke -------------------------------------------------------------------
def test_only_the_latest_keystrokes_hits_show_and_the_query_in_flight_is_cancelled(
    qapp, spin, project: Project
):
    index = Index(list(FILES), delay=0.3)
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs)
    _ctx, host = host_on(engine)
    host.search.everything = everything(index)
    spin(until=lambda: host.rows() != [])

    host.search.query("f")
    host.search.query("f3")  # before "f"'s answer: that query is cancelled, its hits dropped
    spin(until=lambda: hits_of(host) != [], timeout=5)
    assert hits_of(host) == ["a/deep/f3.jpg", "a/deep/f3.txt"]
    # "f" was skipped before it ran, or cancelled while it ran (its probe or its search);
    # "f3" ran to the end: its probe and its search
    assert index.cancelled[-2:] == [False, False] and all(index.cancelled[:-2])
    assert host.worker.last_thread != threading.get_ident()  # searched off the GUI thread
    snap = host.snapshot()["search"]
    assert snap == {"text": "f3", "hits": hits_of(host), "more": False, "backend": "everything"}

    host.search.query("")  # cleared: back to the tree
    assert hits_of(host) == [] and host.search.result is None
    host.dispose()
    jobs.shutdown()


def test_hits_pass_the_trees_screener_and_the_note_says_when_there_are_more(project: Project):
    engine = Engine(project=project, jobs=InlineJobManager())
    doc = engine.live.document()
    doc["links"]["l1"]["params"]["screener"] = ["*.txt"]  # the tree shows text only
    engine.live.submit(doc, origin="agent:s1", label="text only")
    _ctx, host = host_on(engine)
    many = [f"a/{i:03d}-hit.txt" for i in range(250)]
    index = Index(list(FILES) + many)
    host.search.everything = everything(index)

    host.search.query("f3")
    assert hits_of(host) == ["a/deep/f3.txt"]  # the jpg hit was screened out
    assert index.queries[-1].endswith(' file: "f3"')
    host.search.query("hit")
    assert len(hits_of(host)) == 200 and host.search.result.more
    host.search.query("nothing-here")
    assert hits_of(host) == [] and host.search.result.backend == "everything"


# -- hits as rows -------------------------------------------------------------------------------
def test_a_hit_click_picks_double_click_opens_and_reveal_returns_to_the_tree_at_it(
    project: Project,
):
    engine = Engine(project=project, jobs=InlineJobManager())
    ctx, host = host_on(engine)
    host.search.everything = everything(Index(up=False))  # the scan answers

    host.search.query("f3")
    assert host.search.result.backend == "scan"
    assert hits_of(host) == ["a/deep/f3.jpg", "a/deep/f3.txt"]
    assert host.search.click(1)
    assert picks(ctx).paths == ["a/deep/f3.txt"]
    assert picks(ctx).stats == {
        "a/deep/f3.txt": [3, (project.root / "a" / "deep" / "f3.txt").stat().st_mtime_ns]
    }
    assert engine.live.data["views"]["fege"]["params"]["selected"] == ["a/deep/f3.txt"]
    assert host.search.model.rows()[1]["selected"] is True
    assert host.search.click(0, CTRL)  # Ctrl toggles another in
    assert picks(ctx).paths == ["a/deep/f3.jpg", "a/deep/f3.txt"]
    assert host.snapshot()["loaded"] == [""]  # nothing listed for the picks

    opened = host.search.open(1)
    assert opened is not None and opened.action == "created"
    assert engine.live.data["views"][opened.view]["type"] == "preview"
    # the gesture picked the file in params; the registry's patch brings it to the host,
    # which lists the folders above it and selects it there too
    assert engine.live.data["views"]["fege"]["params"]["selected"] == ["a/deep/f3.txt"]
    host.on_patch([{"op": "replace", "path": "/views/fege/params/selected"}], {})
    assert picks(ctx).paths == ["a/deep/f3.txt"] and host.model.selected == {"a/deep/f3.txt"}

    # a right-click on a hit: its menu names the picked files, the hit first
    target = host.context_target({"kind": "hit", "row": 1})
    assert [it.key for it in target.items] == ["a/deep/f3.txt"]

    assert host.search.reveal(1)
    assert host.search.text == "" and hits_of(host) == []
    assert host.model.selected == {"a/deep/f3.txt"} and {"a", "a/deep"} <= host.model.expanded
    assert host.snapshot()["loaded"] == ["", "a", "a/deep"]
    assert picks(ctx).paths == ["a/deep/f3.txt"]


def test_the_scan_answers_when_everything_is_unavailable_and_a_miss_names_the_backend(
    project: Project,
):
    engine = Engine(project=project, jobs=InlineJobManager())
    _ctx, host = host_on(engine)
    notes: list[str] = []

    class Item:
        def setProperty(self, name, value):
            if name == "searchNote":
                notes.append(value)

    host.item = Item()  # type: ignore[assignment]
    host.search.everything = everything(Index(up=False))
    host.search.query("nope")
    assert host.search.result.backend == "scan" and hits_of(host) == []
    assert notes[-1] == "no matches · searched with a scan of the folder"
    host.search.query("z")
    assert hits_of(host) == ["b/z.txt"] and notes[-1] == "1 hit"
    host.item = None


# -- the real shell -------------------------------------------------------------------------
def test_typing_in_the_focused_tree_searches_and_escape_returns_to_the_tree(
    app, spin, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(fs, "_everything", everything(Index(up=False)))  # the scan answers
    root = app.window.contentItem()
    spin(until=lambda: find_item(root, "treeRow:fege:a") is not None)
    host = app.hosts.hosts["fege"]
    field = find_item(root, "treeSearch:fege")
    tree_list, hits = find_item(root, "fileTreeList:fege"), find_item(root, "fileTreeHits:fege")
    assert field is not None and tree_list.isVisible() and not hits.isVisible()

    # a click focuses the tree; typing then lands in the search box
    row = find_item(root, "treeRow:fege:top.md")
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        row.mapToScene(row.boundingRect().center()).toPoint(),
    )
    spin(30)
    for ch in "f3":
        QTest.keyClick(app.window, ch)
    spin(until=lambda: find_item(root, "searchHit:fege:a/deep/f3.txt") is not None)
    assert field.property("text") == "f3" and host.search.text == "f3"
    assert hits.isVisible() and not tree_list.isVisible()
    caption = find_item(root, "fileTreeCaption:fege")
    assert caption.property("text") == "2 hits"
    assert find_item(root, "searchHitFolder:fege:a/deep/f3.txt").property("text") == "a/deep"

    hit = find_item(root, "searchHit:fege:a/deep/f3.txt")
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        hit.mapToScene(hit.boundingRect().center()).toPoint(),
    )
    spin(
        until=lambda: (
            app.engine.links.output_of("fege", "selected_files").paths == ["a/deep/f3.txt"]
        )
    )
    assert app.live.data["views"]["fege"]["params"]["selected"] == ["a/deep/f3.txt"]

    QTest.keyClick(app.window, Qt.Key_Escape)
    spin(until=lambda: field.property("text") == "")
    assert tree_list.isVisible() and not hits.isVisible() and host.search.text == ""
    assert (
        "fege" in app.live.data["views"]
    )  # the shell's Escape did not clear the canvas selection first
    assert caption.property("text").endswith("selected")
