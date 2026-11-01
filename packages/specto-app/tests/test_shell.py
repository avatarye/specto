"""The shell offscreen (#39): the app opens the project's canvas on ``engine.live``, the
file tree is fed from the project folder, a submitted composition renders and follows
the file tree's selection, close is a user submit, undo reverts it, snap and
shell.locate see the window, and the endpoint serves the SDK."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from gallery_helpers import copy_frames, needs_testdata
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from specto_core import CallContext, Project
from specto_sdk import Client
from specto_sdk.transport import open_transport

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin
from PySide6.QtQuick import QQuickItem

from specto_app.app import find_item
from specto_app.views.file_tree import FileTreeHost
from specto_app.views.gallery import GalleryHost

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
FRAME_KEYS = r"(?P<frame>\d{5})_\d+\.jpg$"

pytestmark = needs_testdata


@pytest.fixture
def frames_project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "frames", "frames")
    copy_frames(proj.root / "runs" / "cam-a", 6)
    return proj


@pytest.fixture
def app(qapp, frames_project, spin, monkeypatch):
    from specto_app.app import SpectoApp

    monkeypatch.setenv("SPECTO_CACHE_DIR", str(frames_project.root.parent / "cache"))
    a = SpectoApp(frames_project, jobs="inline", web=False, offscreen=True, serve=True)
    a.start()
    spin(80)
    yield a
    a.close()
    spin(20)


def gallery_doc(app, port: str = "selected_files") -> dict:
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Cam A",
        "layout": {"position": [300, 0], "size": [640, 400]},
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "selection" if port == "selected_files" else "pinned",
        "label": "cam-a frames",
        "source": {"view": "fege" if port == "selected_files" else "project", "port": port},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["runs/cam-a"], "screener": ["@images"], "keys": FRAME_KEYS},
        "summary": {"sentence": "The gallery shows the cam-a frames picked in the file tree."},
    }
    return doc


def submit(app, doc: dict, label: str) -> dict:
    return app.engine.call("canvas.submit", {"document": doc, "label": label}, AGENT)


def click_item(app, name: str, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    """A real click through the window (QML handlers see the modifiers)."""
    item = find_item(app.window.contentItem(), name)
    assert item is not None, name
    p = item.mapToScene(item.boundingRect().center()).toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, modifiers, p)


# -- opening ---------------------------------------------------------------------------------
def test_startup_shows_the_file_tree_fed_from_the_project(app, spin):
    assert [e["id"] for e in app.controller.viewIds] == ["fege"]
    # the window opens so the window-frame guide (1600x900) fits the canvas area
    area = find_item(app.window.contentItem(), "canvasArea")
    assert (area.width() - 24, area.height() - 24) == (1600, 900)
    spin(until=lambda: "fege" in app.hosts.hosts)
    tree = app.hosts.hosts["fege"]
    assert isinstance(tree, FileTreeHost)
    assert [r["path"] for r in tree.rows()][:3] == ["runs", "runs/cam-a"] + [
        r for r in [tree.rows()[2]["path"]] if r.startswith("runs/cam-a/00000")
    ]
    slot = app.hosts.slot_of("fege")
    assert slot is not None and slot.objectName() == "slot:fege"
    assert tree.item.parentItem() is slot and slot.width() > 100
    assert app.controller.views["fege"]["health"] == "ok"
    assert app.endpoint is not None and app.endpoint.listening


# -- the first dataflow ------------------------------------------------------------------------
def test_submitted_composition_renders_and_follows_the_file_tree(app, spin):
    res = submit(app, gallery_doc(app), "a gallery of the picked frames")
    assert res["views"]["added"] == ["kobi"] and res["links"]["added"] == ["l2"]
    spin(until=lambda: "kobi" in app.hosts.hosts)
    gallery = app.hosts.hosts["kobi"]
    if not isinstance(gallery, GalleryHost):
        pytest.skip(f"gallery host unavailable here: {getattr(gallery, 'reason', '')}")
    assert [e["id"] for e in app.controller.viewIds] == ["fege", "kobi"]
    left = app.hosts.slot_of("fege").mapToScene(QPointF(0, 0)).x()
    assert app.hosts.slot_of("kobi").mapToScene(QPointF(0, 0)).x() > left  # side by side
    assert gallery.gallery.count() == 0 and app.controller.views["kobi"]["health"] == "paused"
    tree: FileTreeHost = app.hosts.hosts["fege"]
    rows = tree.rows()
    jpgs = [i for i, r in enumerate(rows) if r["path"].endswith(".jpg")]

    click_item(app, f"treeRow:fege:{rows[jpgs[2]]['path']}")
    spin(until=lambda: gallery.gallery.count() == 1)
    assert gallery.keys == ["00002"] and app.controller.views["kobi"]["health"] == "ok"
    click_item(app, f"treeRow:fege:{rows[jpgs[5]]['path']}", Qt.KeyboardModifier.ShiftModifier)
    spin(until=lambda: gallery.gallery.count() == 4)
    assert gallery.keys == ["00002", "00003", "00004", "00005"]
    assert gallery.decoded == 4 and gallery.failed == 0
    snap = app.engine.call("snap", {"target": "kobi"})
    assert snap["meta"]["links"]["in"][0]["status"] == "resolved"
    assert snap["meta"]["host"]["count"] == 4
    # the file tree's own undo history is untouched: picking files is not a document edit
    assert app.controller.undoDepth == 1


def test_close_button_is_a_user_submit_and_undo_brings_the_view_back(app, spin):
    submit(app, gallery_doc(app), "gallery")
    spin(until=lambda: "kobi" in app.hosts.hosts)
    click_item(app, "close:kobi")
    spin(until=lambda: "kobi" not in app.hosts.hosts)
    assert "kobi" not in app.live.data["views"] and "l2" not in app.live.data["links"]
    rec = app.live.history[-1]
    assert rec.origin == "user" and rec.label == "close Cam A"
    assert [e["id"] for e in app.controller.viewIds] == ["fege"]
    assert app.controller.canUndo and app.controller.undo()
    spin(until=lambda: "kobi" in app.hosts.hosts)
    assert app.controller.views["kobi"]["label"] == "Cam A" and app.controller.canUndo is not None
    click_item(app, "undoButton")
    spin(until=lambda: "kobi" not in app.hosts.hosts)
    assert app.live.cursor == 0


def test_missing_host_is_a_card_with_health_error(app, spin):
    # every built-in type has a host now; a 3d view with its factory withdrawn (a build
    # without Babylon vendored, a plugin that failed to load) stands for the missing one
    app.hosts.factories.pop("3d", None)
    doc = app.live.document()
    doc["views"]["kobi"] = {"type": "3d", "label": "scene", "layout": {"position": [300, 0]}}
    pin(doc, "kobi")
    submit(app, doc, "3d")
    spin(until=lambda: "kobi" in app.hosts.hosts)
    host = app.hosts.hosts["kobi"]
    assert type(host).__name__ == "MissingHost"
    assert app.controller.views["kobi"]["health"] == "error"
    assert isinstance(host.item, QQuickItem) and host.item.property("viewType") == "3d"


# -- snap and shell.locate -----------------------------------------------------------------------
def test_shell_locate_and_snap_see_the_window(app, spin, tmp_path):
    e = app.engine
    res = e.call("shell.locate", {"name": "undoButton"})
    assert res["found"] and res["visible"] and res["rect"][2] > 10 and res["screen"]
    assert e.call("shell.locate", {"name": "overworldButton"})["found"] is False
    hdr = e.call("shell.locate", {"name": "header:fege"})
    slot = e.call("shell.locate", {"name": "slot:fege"})
    assert hdr["found"] and slot["found"] and hdr["rect"][1] < slot["rect"][1]
    # a text item reports its text (gui_drive assert_text, #101); others report none
    assert e.call("shell.locate", {"name": "title:fege"})["text"].startswith("fege")
    assert slot.get("text") is None
    spin(until=lambda: "fege" in app.hosts.hosts)
    row = e.call("shell.locate", {"name": "treeRow:fege:runs/cam-a"})
    assert row["found"] and row["visible"]

    out = tmp_path / "v1.png"
    res = e.call("snap", {"target": "fege", "image": True, "path": str(out), "layout": True})
    assert res["kind"] == "view" and out.exists() and res["image_size"][0] < app.window.width()
    assert res["meta"]["host"]["files"] == 12 and res["meta"]["links"]["in"][0]["id"] == "l1"
    assert res["layout"].startswith('view fege "Files" file_tree at=280x900@80,0 health=ok px=')
    res = e.call("snap", {"target": "canvas", "image": True, "layout": True})
    assert res["kind"] == "canvas" and ".specto" in res["image"] and "snaps" in res["image"]
    assert res["image_size"] == [app.window.width(), app.window.height()]
    lines = res["layout"].splitlines()
    assert lines[0].startswith('canvas "frames" window_frame=1600x900@0,0')
    assert any(
        ln.startswith("  link l1 intaker project.all_files -> fege.data resolved") for ln in lines
    )
    assert e.call("snap", {"target": "@Files"})["target"] == "fege"
    # window.raise (#215): the window is shown on top and named; offscreen it still "raises"
    res = e.call("window.raise", {})
    assert res == {"raised": True, "instance": app.instance_name} and app.window.isVisible()


# -- endpoint through the SDK against the running app ------------------------------------------
def test_endpoint_roundtrip_through_sdk(app, spin):
    info = app.endpoint.info
    doc = gallery_doc(app)
    box: dict = {}

    def client_work():
        try:
            c = Client(open_transport(info), origin="agent:sdk", client="t")
            box["hello"] = c.hello_result
            box["before"] = c.call("canvas.state")
            box["submit"] = c.call("canvas.submit", {"document": doc, "label": "sdk gallery"})
            box["snap"] = c.call("snap", {"target": "canvas", "layout": True})
            box["undo"] = c.call("canvas.undo")
            c.close()
        except BaseException as e:
            box["error"] = e

    th = threading.Thread(target=client_work, daemon=True)
    th.start()
    spin(until=lambda: not th.is_alive(), timeout=15)
    if "error" in box:
        raise box["error"]
    assert box["hello"]["project"]["id"] == app.project.id
    assert list(box["before"]["document"]["views"]) == ["fege"]
    assert box["submit"]["views"]["added"] == ["kobi"]
    assert 'view kobi "Cam A" gallery' in box["snap"]["layout"]
    assert box["undo"]["txn"] == box["submit"]["txn"]
    spin(until=lambda: "kobi" not in app.hosts.hosts)
    assert [e["id"] for e in app.controller.viewIds] == ["fege"]


def test_recents_entry_marks_the_open_project(app):
    mine = [e for e in app.recents if e["current"]]
    assert mine and mine[0]["root"] == str(app.project.root)


def test_escape_closes_the_gallery_quick_look_before_the_shell(app, spin):
    """The shell's window-wide Escape shortcut must not swallow the key while the
    gallery's quick look is open."""
    submit(app, gallery_doc(app, "all_files"), "gallery")
    spin(until=lambda: "kobi" in app.hosts.hosts)
    host = app.hosts.hosts["kobi"]
    if not isinstance(host, GalleryHost):
        pytest.skip("gallery host unavailable here")
    spin(until=lambda: host.gallery.count() == 6)
    root, g = host.item, host.gallery.item
    g.forceActiveFocus()
    g.setProperty("selectedIndex", 3)
    spin(20)
    QTest.keyClick(app.window, Qt.Key.Key_Space)
    spin(20)
    assert root.property("quickIndex") == 3
    QTest.keyClick(app.window, Qt.Key.Key_Escape)
    spin(20)
    assert root.property("quickIndex") == -1


# -- an empty project folder -------------------------------------------------------------------


def test_an_empty_project_folder_says_so_and_the_tree_fills_when_files_arrive(
    qapp, project, spin, monkeypatch
):
    """A new folder with nothing in it: the tree says it has no files yet instead of
    standing blank; a file dropped in shows up on its own, without a refresh or a
    relaunch (the file tree auto-updates by default, #115)."""
    from specto_app.app import SpectoApp

    a = SpectoApp(
        project, jobs="inline", web=False, offscreen=True, serve=False, watch_interval_s=0.05
    )
    a.start()
    try:
        spin(until=lambda: "fege" in a.hosts.hosts)
        tree = a.hosts.hosts["fege"]
        spin(until=lambda: a.controller.views["fege"]["health"] == "ok")
        assert tree.rows() == []
        empty = find_item(a.window.contentItem(), "fileTreeEmpty:fege")
        assert empty is not None and empty.isVisible()
        assert "no files yet" in empty.property("text")
        assert tree._watch.isActive()  # the tree's own watch (#231): the runtime runs none

        (project.root / "notes.md").write_text("hello", encoding="utf-8")
        spin(until=lambda: [r["path"] for r in tree.rows()] == ["notes.md"], timeout=8.0)
        assert not empty.isVisible()
    finally:
        a.close()
        spin(20)
