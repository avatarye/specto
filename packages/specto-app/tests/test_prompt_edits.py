"""Partial submits in the running shell, offscreen (#98, seams 2 and 3): a scripted agent
runs the spec's two worked prompts as edits through the real prompt bar. The strip shows
the submit's label (no closing reply), Undo reverts the turn, only the touched views are
outlined, a selection intaker added by edits receives the file tree's picks, and a view
the user drags while the agent works stays where it was dropped."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item
from specto_app.prompt.reference import EDITS_EXAMPLES
from specto_app.prompt.scripted import ScriptedAgentSession
from specto_core.links import FilesValue

NOMOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
SEED = ("runs/front/00001_1.jpg", "runs/front/00002_1.jpg", "runs/rear/00001_1.jpg")


@pytest.fixture
def project(tmp_path):
    from specto_core import Project

    proj = Project.init(tmp_path / "proj", "demo")
    for name in SEED:
        p = proj.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    return proj


@pytest.fixture
def app(qapp, project, aloop, spin):
    """The app with a scripted agent; a test sets ``app.script`` (its turns) first."""
    from specto_app.app import SpectoApp

    def factory(tools):
        return ScriptedAgentSession(tools, a.script)

    a = SpectoApp(
        project, jobs="inline", web=False, offscreen=True, serve=False, agent=factory, loop=aloop
    )
    a.script = []  # type: ignore[attr-defined]
    a.start()
    spin(50)
    yield a
    a.close()
    spin(20)


def example(prompt: str) -> dict:
    return dict(next(args for p, args in EDITS_EXAMPLES if p == prompt))


def tree_of(app) -> str:
    return next(v for v, d in app.live.data["views"].items() if d["type"] == "file_tree")


def run_prompt(app, aspin, text: str) -> list[list[str]]:
    bar = app.prompt_bar
    outlined: list[list[str]] = []
    bar.touched.connect(lambda ids: outlined.append(list(ids)))
    assert bar.submit(text) is True
    aspin(until=lambda: not bar.busy)
    return outlined


def test_a_new_gallery_by_edits_follows_the_picks_and_one_undo_reverts_it(app, aspin, spin):
    tree = tree_of(app)
    args = example("show the selected folder as a gallery")
    edits = args["edits"]
    edits["links"]["new2"]["source"]["view"] = tree
    app.script = [[{"tool": "submit_canvas", "args": args}]]
    before = app.live.document()

    outlined = run_prompt(app, aspin, "show the selected folder as a gallery")

    bar = app.prompt_bar
    (name,) = set(app.live.data["views"]) - set(before["views"])
    assert app.live.data["links"]["l2"]["target"] == {"view": name, "port": "data"}
    assert bar.status == "changed" and bar.summary == "add a gallery of the picked images"
    assert bar.replyText == bar.summary  # no closing reply: the label is the reply
    assert app.controller.undoDepth == 1
    assert {v for v in outlined[-1] if v in app.live.data["views"]} == {name}

    # seam 2: the selection intaker the edit added receives the file tree's picks
    spin(until=lambda: name in app.hosts.hosts)
    picked = ["runs/front/00002_1.jpg"]
    app.engine.links.emit(
        tree, "selected_files", FilesValue(root=str(app.project.root), paths=picked)
    )
    spin(until=lambda: getattr(app.engine.links.input_of(name, "data"), "items", None))
    items = app.engine.links.input_of(name, "data").items
    assert [i.primary.replace("\\", "/").endswith("00002_1.jpg") for i in items] == [True]

    assert bar.undoTurn() is True
    assert app.live.document() == before


def add_galleries(app, spin) -> None:
    doc = app.live.document()
    for vid, x, y in (("kobi", 700, 460), ("tamu", 1000, 40)):
        doc["views"][vid] = {
            "type": "gallery",
            "label": vid,
            "layout": {"position": [x, y], "size": [400, 300]},
        }
        pin(doc, vid)
    app.live.submit(doc, origin="user", label="setup")
    spin(until=lambda: "tamu" in app.hosts.hosts)


def drag_header(app, spin, vid: str, dx: int, dy: int) -> None:
    item = find_item(app.window.contentItem(), f"header:{vid}")
    assert item is not None
    r = item.boundingRect()
    p0 = item.mapToScene(r.topLeft() + QPoint(int(r.width() * 0.3), int(r.height() * 0.5)))
    p0 = p0.toPoint()
    QTest.mousePress(app.window, LEFT, NOMOD, p0)
    for i in range(1, 5):
        QTest.mouseMove(app.window, p0 + QPoint(dx * i // 4, dy * i // 4))
        spin(5)
    QTest.mouseRelease(app.window, LEFT, NOMOD, p0 + QPoint(dx, dy))
    spin(10)


def test_a_view_dragged_while_the_agent_moves_another_stays_where_it_was_dropped(app, aspin, spin):
    add_galleries(app, spin)
    args = example("move kobi next to the file tree")
    app.script = [[{"wait_ms": 300}, {"tool": "submit_canvas", "args": args}]]
    bar = app.prompt_bar
    assert bar.submit("move kobi next to the file tree") is True
    aspin(until=lambda: bool(app.prompt_bar.tools.active))

    # the agent is "thinking" (its loop is not stepped while the user drags)
    drag_header(app, spin, "tamu", 60, 80)
    dropped = list(app.live.data["views"]["tamu"]["layout"]["position"])
    assert dropped != [1000.0, 40.0] and app.live.history[-1].origin == "user"
    aspin(until=lambda: not bar.busy)

    views = app.live.data["views"]
    assert views["kobi"]["layout"]["position"] == [330.0, 0.0]
    assert views["tamu"]["layout"]["position"] == dropped
    assert bar.status == "changed" and bar.attemptsFailed == 0

    # the strip's Undo reverts the agent's move only; the user's drag stays
    assert bar.undoTurn() is True
    assert app.live.data["views"]["kobi"]["layout"]["position"] == [700.0, 460.0]
    assert app.live.data["views"]["tamu"]["layout"]["position"] == dropped
