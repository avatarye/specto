"""Seam 2 (#232, SPEC-38): the file tree's type-to-search. Only the latest keystroke's
hits show and the query in flight is cancelled; hits pass the tree's screener and the
note says when there are more; while there is text the tree shows the hits inside their
folders (every folder above a hit open, a folder that matches by itself closed until
opened, which lists it), built from the hits alone with nothing listed in the tree;
every gesture works on those rows as on the tree's own (click, Ctrl, Shift, a folder
pick, double-click, the context menu, drag, the chevron), and reveal returns to the tree
at a hit; clearing brings the tree back as it was, a pick made meanwhile kept; the scan
answers when Everything is unavailable and a miss names the backend; in the real shell,
typing in the focused tree starts a search, the list shows the hits in their folders,
and Escape returns to the tree. Headless hosts and the offscreen shell over the real
engine."""

from __future__ import annotations

import re
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
SHIFT = int(Qt.KeyboardModifier.ShiftModifier.value)
FILES = {
    "a/f1.txt": b"1",
    "a/f2.txt": b"22",
    "a/deep/f3.txt": b"333",
    "a/deep/f3.jpg": b"jpg",
    "b/sub/x.txt": b"x",
    "b/z.txt": b"z",
    "top.md": b"#",
}
#: an empty folder: a folder hit with nothing under it
EMPTY = "b/void"


class Index:
    """A stand-in Everything: up or down, answering ``hits`` under any root (a path
    ending in ``/`` is a folder), with the folders above them unless the query asks for
    files only."""

    def __init__(self, hits: list[str] | None = None, *, up: bool = True, delay: float = 0.0):
        self.up = up
        self.hits = hits or []
        self.delay = delay
        self.queries: list[str] = []
        self.cancelled: list[bool] = []

    def available(self) -> bool:
        return self.up

    def search(self, query: str, *, limit: int, offset: int = 0, cancelled=None, sort=0, keep=None):
        self.queries.append(query)
        base = query.split('"')[1]
        if self.delay:
            t_end = time.monotonic() + self.delay
            while time.monotonic() < t_end:
                time.sleep(0.01)
                if cancelled is not None and cancelled():
                    break
            self.cancelled.append(bool(cancelled and cancelled()))
        # every path:"..." term against the whole path and every regex:"..." searched
        # in it, as Everything does; a query without either (the probe: is the root
        # indexed at all?) matches every file
        terms = [t.lower() for t in re.findall(r'path:"([^"]*)"', query)]
        regexes = re.findall(r'regex:"([^"]*)"', query)
        entries: dict[str, Hit] = {}
        for p in self.hits:
            if " file:" not in query:
                parts = p.split("/")
                for i in range(1, len(parts)):
                    folder = base + "\\".join(parts[:i])
                    entries.setdefault(folder, Hit(folder, 0, 1, is_dir=True))
            full = base + p.rstrip("/").replace("/", "\\")
            entries[full] = Hit(full, 0, 1, is_dir=True) if p.endswith("/") else Hit(full, 7, 1)
        out = [
            h
            for full, h in sorted(entries.items())
            if all(t in full.lower() for t in terms)
            and all(re.search(r, full, re.IGNORECASE) for r in regexes)
        ]
        page = out[offset : offset + limit]
        return [h for h in page if keep is None or keep(h.path, h.is_dir)], len(out)


def everything(index: Index) -> fs.EverythingSearch:
    return fs.EverythingSearch(index, probe_ttl_s=0)  # type: ignore[arg-type]


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "search")
    for rel, data in FILES.items():
        p = proj.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (proj.root / EMPTY).mkdir()
    return proj


def host_on(engine: Engine) -> tuple[HostContext, FileTreeHost]:
    ctx = HostContext(engine, None)
    host = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
    ctx.links.attach("fege", host.receive)
    return ctx, host


def hits_of(host: FileTreeHost) -> list[str]:
    """The latest query's hits, best first (files and folders)."""
    return host.search.snapshot()["hits"]


def shown(host: FileTreeHost) -> list[tuple[str, int, bool]]:
    """The rows the tree shows: (path, depth, open) for a folder, (path, depth, False)
    for a file."""
    return [(r["path"], r["depth"], r["expanded"]) for r in host.rows()]


def row(host: FileTreeHost, path: str) -> int:
    return [r["path"] for r in host.rows()].index(path)


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
    assert shown(host) == [("a", 0, False), ("b", 0, False), ("top.md", 0, False)]
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
    # files and folders alike, word by word, under the root
    assert index.queries[-1].endswith('\\" path:"f3"') and " file:" not in index.queries[-1]
    host.search.query("deep")  # the folder named after the word, then the files under it
    assert hits_of(host) == ["a/deep", "a/deep/f3.txt"]
    host.search.query("sub/x")
    assert hits_of(host) == ["b/sub/x.txt"]
    host.search.query("hit")
    assert len(hits_of(host)) == 200 and host.search.result.more
    host.search.query("nothing-here")
    assert hits_of(host) == [] and host.search.result.backend == "everything"


# -- the hits in their folders --------------------------------------------------------------
@pytest.mark.parametrize("backend", ["scan", "everything"])
def test_the_tree_shows_the_hits_inside_their_folders_built_from_the_hits_alone(
    project: Project, backend: str
):
    engine = Engine(project=project, jobs=InlineJobManager())
    _ctx, host = host_on(engine)
    index = Index([*FILES, EMPTY + "/"], up=backend == "everything")
    host.search.everything = everything(index)

    host.search.query("f3")
    assert host.search.result.backend == backend
    # every folder above a hit open, nothing else: no b/, no top.md, no f1.txt
    assert shown(host) == [
        ("a", 0, True),
        ("a/deep", 1, True),
        ("a/deep/f3.jpg", 2, False),
        ("a/deep/f3.txt", 2, False),
    ]
    # the tree's own folders were never listed for it, and its rows are as they were
    assert host.snapshot()["loaded"] == [""]
    assert [r["path"] for r in host.model.rows()] == ["a", "b", "top.md"]

    # a folder that matches by itself: shown; open when it holds hits
    host.search.query("sub")
    assert shown(host) == [("b", 0, True), ("b/sub", 1, True), ("b/sub/x.txt", 2, False)]
    # an empty one stays closed; its details line has its own time
    host.search.query("void")
    assert shown(host) == [("b", 0, True), ("b/void", 1, False)]
    void = host.rows()[1]
    assert void["is_dir"] and void["stamp"] and void["size"] == ""
    # opening it lists it (on demand, as any folder of the tree): found empty
    assert host.toggle(1)
    assert shown(host) == [("b", 0, True), ("b/void", 1, True)]
    assert host.rows()[1]["size"] == "empty"
    assert host.snapshot()["loaded"] == [""]  # listed for the search's rows, not the tree's

    # the details line: a file's size and time from the search, a folder above the hits
    # says how many hits are under it (its own count is not known)
    host.search.query("f3")
    rows = {r["path"]: r for r in host.rows()}
    assert rows["a"]["size"] == "2 hits" and rows["a/deep"]["size"] == "2 hits"
    assert rows["a/deep/f3.txt"]["size"] and rows["a/deep/f3.txt"]["stamp"]


def test_every_gesture_works_on_the_hit_rows_as_on_the_trees_own(project: Project):
    from specto_app.views.preview_view import PREVIEW_PLUGIN

    engine = Engine(project=project, jobs=InlineJobManager())
    engine.plugins.register(PREVIEW_PLUGIN)
    ctx, host = host_on(engine)
    host.search.everything = everything(Index(up=False))  # the scan answers

    host.search.query("f3")
    txt, jpg = row(host, "a/deep/f3.txt"), row(host, "a/deep/f3.jpg")
    # a click picks the file, with the stat the search saw; params remember it
    assert host.click(txt)
    assert picks(ctx).paths == ["a/deep/f3.txt"]
    assert picks(ctx).stats == {
        "a/deep/f3.txt": [3, (project.root / "a" / "deep" / "f3.txt").stat().st_mtime_ns]
    }
    assert engine.live.data["views"]["fege"]["params"]["selected"] == ["a/deep/f3.txt"]
    assert host.rows()[txt]["selected"] is True and host.rows()[jpg]["selected"] is False
    assert host.click(jpg, CTRL)  # Ctrl toggles another in
    assert picks(ctx).paths == ["a/deep/f3.jpg", "a/deep/f3.txt"]
    assert host.click(jpg) and host.click(txt, SHIFT)  # Shift picks the range of rows
    assert host.model.selected == {"a/deep/f3.jpg", "a/deep/f3.txt"}
    assert host.snapshot()["loaded"] == [""]  # nothing listed for the picks

    # a folder pick stands for every file under it, not just the hits
    assert host.click(row(host, "a"))
    assert picks(ctx).paths == ["a/deep/f3.jpg", "a/deep/f3.txt", "a/f1.txt", "a/f2.txt"]

    # the context menu and a drag act on the row, or the picks it is in
    assert host.click(txt)
    target = host.context_target({"kind": "items", "row": jpg})
    assert [it.key for it in target.items] == ["a/deep/f3.jpg"]
    assert target.data == {"clicked": "a/deep/f3.jpg"}
    assert host.click(jpg, CTRL)
    target = host.context_target({"kind": "items", "row": txt})
    assert [it.key for it in target.items] == ["a/deep/f3.jpg", "a/deep/f3.txt"]
    assert [it.key for it in host.context_target(None).items] == ["a/deep/f3.jpg", "a/deep/f3.txt"]
    dragged = host.drag_payload({"kind": "items", "row": row(host, "a")})
    assert [it.key for it in dragged.items] == ["a"]
    assert host.context_target({"kind": "items", "row": row(host, "a")}).items[0].is_dir

    # a double-click opens a file's dedicated view (SPEC-32), Ctrl the contextual preview
    own = host.open_row(txt)
    assert own is not None and (own.action, own.mode) == ("created", "dedicated")
    assert engine.live.data["views"][own.view]["type"] != "preview"
    opened = host.open_row(txt, CTRL)
    assert opened is not None and opened.action == "created"
    assert engine.live.data["views"][opened.view]["type"] == "preview"
    assert host.open_row(row(host, "a")) is None  # a folder is not opened
    # the gesture picked the file in params; the registry's patch brings it to the host
    assert engine.live.data["views"]["fege"]["params"]["selected"] == ["a/deep/f3.txt"]
    host.on_patch([{"op": "replace", "path": "/views/fege/params/selected"}], {})
    assert picks(ctx).paths == ["a/deep/f3.txt"] and host.model.selected == {"a/deep/f3.txt"}
    assert host.rows()[row(host, "a/deep/f3.txt")]["selected"] is True

    # the chevron closes and opens a folder of the search's tree
    assert host.toggle(row(host, "a/deep"))
    assert shown(host) == [("a", 0, True), ("a/deep", 1, False)]
    assert host.toggle(row(host, "a/deep"))
    assert len(shown(host)) == 4

    # Enter in the box opens the best hit
    assert host.search.open_first() is not None

    # reveal: back to the tree at the hit, its folders listed and opened, the row picked
    assert host.search.reveal(row(host, "a/deep/f3.txt"))
    assert host.search.text == "" and hits_of(host) == []
    assert host.model.selected == {"a/deep/f3.txt"} and {"a", "a/deep"} <= host.model.expanded
    assert host.snapshot()["loaded"] == ["", "a", "a/deep"]
    assert picks(ctx).paths == ["a/deep/f3.txt"]
    assert "a/deep/f3.txt" in [r["path"] for r in host.rows()]


def test_clearing_the_search_brings_the_tree_back_as_it_was(project: Project):
    engine = Engine(project=project, jobs=InlineJobManager())
    ctx, host = host_on(engine)
    host.search.everything = everything(Index(up=False))
    host.toggle(row(host, "b"))  # the tree: b/ open, top.md picked
    host.click(row(host, "top.md"))
    before_rows, before_open = host.rows(), set(host.model.expanded)

    host.search.query("f3")
    host.toggle(row(host, "a"))  # closing a folder of the search's tree
    host.search.query("")
    assert host.rows() == before_rows and host.model.expanded == before_open
    assert host.model.selected == {"top.md"} and picks(ctx).paths == ["top.md"]

    # a pick made during the search is kept: the latest gesture, already sent on
    host.search.query("z")
    host.click(row(host, "b/z.txt"))
    host.search.clear()
    assert host.model.selected == {"b/z.txt"} and picks(ctx).paths == ["b/z.txt"]
    assert host.model.expanded == before_open
    assert host.rows()[row(host, "b/z.txt")]["selected"] is True  # b/ is open: it shows


def test_picks_set_from_outside_during_a_search_wait_for_it_to_clear(project: Project):
    """A received selection or ``params.selected`` written from outside while a query is
    active: the picks equal it at once (paths neither tree holds included) and the
    search tree shows them, but the hidden tree is neither listed nor opened until the
    search clears; then it shows them as it would have."""
    from specto_core.links import Selection

    engine = Engine(project=project, jobs=InlineJobManager())
    ctx, host = host_on(engine)
    host.search.everything = everything(Index(up=False))
    host.search.query("f3")

    host.receive("selection", Selection(keys=["a/deep/f3.txt"], last="a/deep/f3.txt"))
    assert host.model.selected == {"a/deep/f3.txt"} and picks(ctx).paths == ["a/deep/f3.txt"]
    assert host.rows()[row(host, "a/deep/f3.txt")]["selected"] is True
    assert host.snapshot()["loaded"] == [""] and host.model.expanded == set()
    host.search.query("")
    assert host.snapshot()["loaded"] == ["", "a", "a/deep"]
    assert {"a", "a/deep"} <= host.model.expanded
    assert host.rows()[row(host, "a/deep/f3.txt")]["selected"] is True

    # params.selected from outside (an agent): a file no tree holds yet is kept
    host.search.query("z")
    doc = engine.live.document()
    doc["views"]["fege"]["params"]["selected"] = ["b/sub/x.txt"]
    engine.live.submit(doc, origin="agent:s1", label="pick x")
    host.on_patch([{"op": "replace", "path": "/views/fege/params/selected"}], {})
    assert host.model.selected == {"b/sub/x.txt"} and picks(ctx).paths == ["b/sub/x.txt"]
    assert "b/sub" not in host.snapshot()["loaded"]
    host.search.clear()
    assert host.rows()[row(host, "b/sub/x.txt")]["selected"] is True

    # the user's own pick during the search drops the waiting one: nothing opens on clear
    host.search.query("top")
    host.receive("selection", Selection(keys=["b/z.txt"], last="b/z.txt"))
    host.click(row(host, "top.md"))
    opened = set(host.model.expanded)
    host.search.clear()
    assert host.model.selected == {"top.md"} and host.model.expanded == opened


def test_blank_text_is_no_search_and_ages_follow_the_shown_tree(project: Project):
    engine = Engine(project=project, jobs=InlineJobManager())
    _ctx, host = host_on(engine)
    host.search.everything = everything(Index(up=False))
    host.search.query("   ")
    assert not host.search.active and [r["path"] for r in host.rows()] == ["a", "b", "top.md"]
    host.search.query("f3")
    host.model.clock = lambda: 4_000_000_000.0
    host.bridge.refreshAges()
    assert host.search.model.now == 4_000_000_000.0


def test_a_folder_hit_with_hits_under_it_is_listed_in_full(project: Project):
    """``b`` and ``b/sub`` are folder hits (their names hold the word) with hits under
    them: each opens as the folder it is, listed on the worker, so ``b/void`` (no hit)
    shows beside the hits; a folder only on the way to hits stays narrowed."""
    engine = Engine(project=project, jobs=InlineJobManager())
    _ctx, host = host_on(engine)
    host.search.everything = everything(Index(up=False))
    host.search.query("b")
    assert hits_of(host) == ["b", "b/sub", "b/z.txt", "b/sub/x.txt"]
    assert shown(host) == [
        ("b", 0, True),
        ("b/sub", 1, True),
        ("b/sub/x.txt", 2, False),
        ("b/void", 1, False),
        ("b/z.txt", 1, False),
    ]
    rows = {r["path"]: r for r in host.rows()}
    assert rows["b"]["size"] == "3 items" and rows["b/sub"]["size"].startswith("1 item")
    assert host.snapshot()["loaded"] == [""]  # the search tree's listing, not the tree's
    host.search.query("x.txt")  # b/ and b/sub/ only on the way to the hit: narrowed
    rows = {r["path"]: r for r in host.rows()}
    assert rows["b"]["size"] == "1 hit" and "b/void" not in rows


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

        def property(self, name):
            return None

    host.item = Item()  # type: ignore[assignment]
    host.search.everything = everything(Index(up=False))
    host.search.query("nope")
    assert host.search.result.backend == "scan" and hits_of(host) == []
    assert notes[-1] == "no matches · searched with a scan of the folder"
    host.search.query("z")
    assert hits_of(host) == ["b/z.txt"] and notes[-1] == "1 hit"
    host.item = None


def test_the_tree_comes_back_where_it_was_scrolled(qapp, spin):
    """The list swaps to the search tree and back: the tree's scroll is kept."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuick import QQuickItem, QQuickWindow

    from specto_app.views import gallery_native
    from specto_app.views.file_tree import FILE_TREE_QML, FileTreeModel, SearchTreeModel

    engine = QQmlEngine()
    gallery_native.ensure_import_path(engine)
    tree = FileTreeModel()
    tree.set_paths([f"f{i:03d}.txt" for i in range(300)])
    found = SearchTreeModel(tree)
    found.set_hits([fs.SearchHit("f001.txt", "f001.txt")])
    comp = QQmlComponent(engine, QUrl.fromLocalFile(str(FILE_TREE_QML)))
    item = comp.createWithInitialProperties(
        {"viewId": "v", "treeModel": tree, "searchModel": found}
    )
    assert item is not None, comp.errors()
    win = QQuickWindow()
    win.resize(300, 500)
    item.setParentItem(win.contentItem())
    item.setWidth(300)
    item.setHeight(500)
    win.show()
    spin(30)
    lst = item.findChild(QQuickItem, "fileTreeList:v")
    lst.setProperty("contentY", 1200.0)
    spin(30)
    item.setProperty("searchText", "f001")
    spin(30)
    assert lst.property("count") == 1
    item.setProperty("searchText", "")
    spin(30)
    assert lst.property("count") == 300 and lst.property("contentY") == 1200.0
    win.close()
    item.deleteLater()


# -- the real shell -------------------------------------------------------------------------
def test_typing_in_the_focused_tree_searches_and_escape_returns_to_the_tree(
    app, spin, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(fs, "_everything", everything(Index(up=False)))  # the scan answers
    root = app.window.contentItem()
    spin(until=lambda: find_item(root, "treeRow:fege:a") is not None)
    host = app.hosts.hosts["fege"]
    field = find_item(root, "treeSearch:fege")
    tree_list = find_item(root, "fileTreeList:fege")
    assert field is not None and tree_list.isVisible()
    assert find_item(root, "treeRow:fege:a/deep/f3.txt") is None  # a/ is closed

    # a click focuses the tree; typing then lands in the search box
    top = find_item(root, "treeRow:fege:top.md")
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        top.mapToScene(top.boundingRect().center()).toPoint(),
    )
    spin(30)
    for ch in "f3":
        QTest.keyClick(app.window, ch)
    spin(until=lambda: find_item(root, "treeRow:fege:a/deep/f3.txt") is not None)
    assert field.property("text") == "f3" and host.search.text == "f3"
    # the same list, now the hits in their folders: a/ and a/deep/ open, top.md gone
    assert tree_list.isVisible() and tree_list.property("count") == 4
    assert find_item(root, "treeRow:fege:top.md") is None
    assert find_item(root, "treeChevron:fege:a/deep") is not None
    caption = find_item(root, "fileTreeCaption:fege")
    assert caption.property("text") == "2 hits"

    # the keys: Down from the box to the first hit row, Up and Down move, Up from the top
    # row goes back to the box, Return opens the current file
    assert field.property("activeFocus")
    QTest.keyClick(app.window, Qt.Key_Down)
    spin(30)
    assert tree_list.property("activeFocus") and tree_list.property("currentIndex") == 2
    for expected in (1, 0):
        QTest.keyClick(app.window, Qt.Key_Up)
        spin(20)
        assert tree_list.property("currentIndex") == expected
    QTest.keyClick(app.window, Qt.Key_Up)
    spin(30)
    assert field.property("activeFocus")
    QTest.keyClick(app.window, Qt.Key_Down)
    QTest.keyClick(app.window, Qt.Key_Down)
    spin(30)
    assert tree_list.property("currentIndex") == 3  # a/deep/f3.txt
    views_before = set(app.live.data["views"])
    QTest.keyClick(app.window, Qt.Key_Return)
    spin(until=lambda: set(app.live.data["views"]) != views_before)
    assert len(set(app.live.data["views"]) - views_before) == 1  # its dedicated view
    assert host.search.text == "f3"  # still searching

    hit = find_item(root, "treeRow:fege:a/deep/f3.txt")
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
    spin(until=lambda: find_item(root, "treeRow:fege:top.md") is not None)
    assert tree_list.isVisible() and host.search.text == ""
    assert find_item(root, "treeRow:fege:a/deep/f3.txt") is None  # the tree as it was
    assert (
        "fege" in app.live.data["views"]
    )  # the shell's Escape did not clear the canvas selection first
    assert caption.property("text").endswith("selected")
