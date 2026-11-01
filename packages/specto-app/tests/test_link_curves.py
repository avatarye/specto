"""Curve geometry (user feedback on v2): the controller's wires and their link nodes
follow a view live while it is dragged (move or resize), not only after the drop. The
normal view no longer draws them (#81); the geometry stays for the wiring layer (#82)
and the overworld, which draws them. Offscreen Qt; real mouse input is the scripted flow
tools/gui_flows/v2-fix-frames.txt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from specto_app.app import find_item

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
NOMOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
SENTENCE = "The gallery shows the files picked in the file tree."


def compose(app, spin) -> None:
    """A gallery at (300, 40) fed by the file tree: one link l2 between them."""
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [300, 40], "size": [640, 400]},
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "selection",
        "label": "picked files",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["."], "screener": ["@images"]},
        "summary": {"sentence": SENTENCE},
    }
    app.engine.call("canvas.submit", {"document": doc, "label": "compose"}, AGENT)
    spin(until=lambda: "kobi" in app.hosts.hosts)


def wire(obj, link_id: str) -> dict:
    return next(w for w in obj.wires if w["id"] == link_id)


def ends(obj, link_id: str) -> tuple:
    w = wire(obj, link_id)
    return (w["x2"], w["y2"])


def test_the_curve_follows_a_move_in_progress_and_settles_on_the_drop(app, spin):
    compose(app, spin)
    # at rest the curve ends at the gallery's input port (left edge, on the header line, #121)
    assert ends(app.controller, "l2") == (300 + 2, 40 + 2 + 31)
    # a move in progress (snapped by the controller) moves the end and the node with it
    app.controller.dragMove("kobi", 500.0, 200.0)
    w = wire(app.controller, "l2")
    assert (w["x2"], w["y2"]) == (502, 233) and w["ny"] == (33 + 233) / 2
    spin(10)
    assert find_item(app.window.contentItem(), "wire:l2") is None  # not drawn here (#81)
    # the view is dropped back where it was: the curve settles without a submit
    app.controller.cancelDrag("kobi")
    assert ends(app.controller, "l2") == (302, 73)
    assert app.controller.undoDepth == 1
    # a real drag: press on the header, move, check mid-drag, release
    header = find_item(app.window.contentItem(), "header:kobi")
    p0 = header.mapToScene(header.boundingRect().topLeft() + QPoint(60, 15)).toPoint()
    QTest.mousePress(app.window, LEFT, NOMOD, p0)
    QTest.mouseMove(app.window, p0 + QPoint(40, 60))
    QTest.mouseMove(app.window, p0 + QPoint(80, 120))
    spin(10)
    assert ends(app.controller, "l2") == (382, 193)
    QTest.mouseRelease(app.window, LEFT, NOMOD, p0 + QPoint(80, 120))
    spin(10)
    assert app.live.data["views"]["kobi"]["layout"]["position"] == [380.0, 160.0]
    assert ends(app.controller, "l2") == (382, 193)


def test_the_curve_follows_a_resize_in_progress(app, spin):
    compose(app, spin)
    # the input port rides the top edge: a resize from the top moves it
    app.controller.dragResize("kobi", 300.0, 100.0, 640.0, 340.0, ["top"])
    assert wire(app.controller, "l2")["y2"] == 100 + 33
    app.controller.cancelDrag("kobi")
    assert wire(app.controller, "l2")["y2"] == 73


def test_in_the_overworld_the_wires_and_link_nodes_follow_a_free_drag(app, spin):
    compose(app, spin)
    ow = app.overworld
    ow.toggle()
    spin(20)
    ow.dragMove("kobi", 700.0, 300.0)
    w = wire(ow, "l2")
    assert (w["x2"], w["y2"]) == (702, 333)
    assert (w["nx"], w["ny"]) == ((358 + 702) / 2, (33 + 333) / 2)
    spin(10)
    node = find_item(app.window.contentItem(), "linkNode:l2")
    assert node is not None and abs(node.x() + node.width() / 2 - w["nx"]) < 0.5
    ow.cancelDrag("kobi")
    assert ends(ow, "l2") == (302, 73)
    # the drop resolves snapped (the tree's right edge, 360) and the wires settle there
    ow.dragMove("kobi", 366.0, 5.0)
    assert ow.endMove("kobi", 366.0, 5.0)
    assert ends(ow, "l2") == (362, 33)
    ow.toggle()
