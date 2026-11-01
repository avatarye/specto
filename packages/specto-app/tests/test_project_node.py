"""The project source drawn as a node (#83): fixed at the canvas origin left of the file
tree, no card body, its one output all_files. Since #127 the node is a rounded tile
showing the identicon seeded "root" (no Specto mark) and has no port of its own: the
tree's input circle already shows root. Its refresh is a port-like circle at the node's
bottom centre. Hovering it says "all files" and the count over "the project folder" (a
port tip, #127); it pans with the canvas, Home brings it back, the overworld shows it
too. It is not a view, so no gesture reaches it."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from specto_app.app import find_item
from specto_app.canvas.chains import PROJECT_NODE, project_block, project_refresh_anchor
from specto_app.canvas.identicon import identicon_url
from specto_app.theme import METRICS

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
PAD = 12


def seed(app, spin) -> None:
    root = Path(app.project.root)
    for name in ("a.txt", "b.txt", "c/d.txt"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(name, encoding="utf-8")
    app.engine.links.resolve("l1")  # the tree listed its root before the files were written
    spin(until=lambda: len(app.hosts.hosts["fege"].rows()) == 3)


def scene_rect(item) -> tuple[float, float, float, float]:
    p = item.mapToScene(item.boundingRect().topLeft())
    return (p.x(), p.y(), item.width(), item.height())


def hover(app, spin, item) -> None:
    QTest.mouseMove(app.window, QPoint(1, 1))
    spin(20)
    QTest.mouseMove(app.window, item.mapToScene(item.boundingRect().center()).toPoint())
    spin(80)


def test_the_node_sits_at_the_canvas_origin_left_of_the_file_tree(app, spin):
    seed(app, spin)
    root = app.window.contentItem()
    node = find_item(root, "projectNode")
    assert node is not None and node.isVisible()
    area = find_item(root, "canvasArea")
    area_x, area_y, _, _ = scene_rect(area)
    nx, ny, nw, nh = scene_rect(node)
    # canvas (0, 0) at the area's padded corner; the node at its fixed canvas spot
    assert (nx - area_x, ny - area_y) == (PAD + PROJECT_NODE[0], PAD + PROJECT_NODE[1])
    tx, _, _, _ = scene_rect(find_item(root, "view:fege"))
    assert nx + nw < tx  # left of the file tree
    # #121: its centre on the line of the fresh file tree's input port (the tree at y 0),
    # so the wire between them runs straight
    _, iy, _, ih = scene_rect(find_item(root, "port:fege:in"))
    assert iy + ih / 2 == ny + nh / 2
    # #127: the node has no port of its own; it is a tile showing the identicon seeded
    # "root" (the project source is in no chain: the neutral colour, like a loose view),
    # and no Specto mark
    assert find_item(node, "port:project:out") is None
    face = find_item(node, "identicon:projectNode")
    assert face is not None and face.isVisible()
    assert face.property("source").toString() == identicon_url("root", None)
    assert app.controller.projectNode["icon"] == identicon_url("root", None)
    fx, fy, fw, fh = scene_rect(face)
    assert abs((fx + fw / 2) - (nx + nw / 2)) <= 0.5 and abs((fy + fh / 2) - (ny + nh / 2)) <= 0.5
    assert find_item(root, "projectMark") is None
    # the input it feeds shows the same neutral grid in its circle (a hub takes no
    # chain's colour): that circle is where root shows as a port
    tree_in = find_item(root, "port:fege:in")
    assert tree_in.property("icon") == identicon_url("root", None, round=True)
    # its refresh: a port-sized circle centred on the node's bottom edge, where the
    # controller (chains.project_refresh_anchor) says
    refresh = find_item(node, "refresh:project")
    assert refresh is not None and refresh.isVisible()
    rx, ry, rw, rh = scene_rect(refresh)
    assert rw == rh == METRICS["port_px"]
    cx, cy = rx + rw / 2 - nx, ry + rh / 2 - ny  # node px
    assert (cx, cy) == (nw / 2, nh)  # bottom centre
    ax, ay = project_refresh_anchor()
    assert (ax, ay) == (PROJECT_NODE[0] + 26, PROJECT_NODE[1] + 52)
    assert app.controller.projectNode["refresh"] == {"x": ax, "y": ay}
    assert (cx, cy) == (ax - PROJECT_NODE[0], ay - PROJECT_NODE[1])
    # a link node keeps clear of the whole node, its refresh circle included
    bx, by, bw, bh = project_block()
    assert bx <= rx - nx and by <= ry - ny and rx - nx + rw <= bx + bw and ry - ny + rh <= by + bh
    # a wire leaves the node's right edge for the tree's input port
    wire = next(w for w in app.controller.wires if w["id"] == "l1")
    x0, y0 = PROJECT_NODE[:2]
    assert (wire["x1"], wire["y1"]) == (x0 + nw, y0 + nh / 2) and wire["source"] == "project"
    assert wire["y2"] == wire["y1"]  # straight across (#121)


def test_hovering_the_node_says_all_files_and_the_count(app, spin):
    seed(app, spin)
    root = app.window.contentItem()
    hover(app, spin, find_item(root, "projectNode"))
    tip = find_item(root, "tip:projectNode")
    assert tip is not None and tip.isVisible()
    # #127: a port tip, DATA only (no link to filter). The count is unknown until a
    # whole-folder intaker scans the project: the file tree never does (#231)
    assert app.engine.links.project_files is None and app.controller.projectNode["count"] == 0
    assert app.controller.projectNode["tip"] == {
        "what": "all files",
        "parts": [{"text": "the project folder"}, {"text": " · double-click: the scratch pad"}],
        "filter": "",
    }
    assert find_item(root, "tipWhat:projectNode").property("text") == "all files"
    assert find_item(root, "tipDataIcon:projectNode").isVisible()
    assert not find_item(root, "tipFilter:projectNode").isVisible()
    # it hangs below the refresh circle, not over it
    _, ry, _, rh = scene_rect(find_item(root, "refresh:project"))
    assert scene_rect(find_item(root, "tipCaret:projectNode"))[1] >= ry + rh - 2
    # the refresh circle's tip says what it does, in an ordinary one-line tip
    refresh = find_item(root, "refresh:project")
    assert refresh.property("tip") == "refresh all · rescan the project"
    hover(app, spin, refresh)
    assert find_item(root, "tip:refresh:project").isVisible()
    assert not tip.isVisible()  # one tip at a time
    # looking is not an edit
    assert app.controller.undoDepth == 0


def test_a_drag_on_the_node_moves_nothing_and_it_pans_with_the_canvas(app, spin):
    root = app.window.contentItem()
    node = find_item(root, "projectNode")
    before = scene_rect(node)
    doc = app.live.document()
    centre = node.mapToScene(node.boundingRect().center()).toPoint()
    QTest.mousePress(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre)
    for step in range(1, 6):
        QTest.mouseMove(app.window, centre + QPoint(40 * step, 30 * step))
        spin(5)
    QTest.mouseRelease(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        centre + QPoint(200, 150),
    )
    spin(30)
    assert scene_rect(node) == before and app.live.document() == doc
    assert app.controller.undoDepth == 0

    app.controller.setPan(500.0, 0.0)
    spin(30)
    assert scene_rect(node)[0] == before[0] - 500
    app.controller.home()  # the node and the tree back at the padded corner
    spin(30)
    assert app.controller.pan == {"x": 0.0, "y": 0.0} and scene_rect(node) == before


def test_a_view_the_user_unlinks_shows_not_connected_until_it_is_relinked(app, spin):
    """Reachability binds the agent only (#83, like a lock): the user may delete a
    view's only link. The view then says "not connected" (its health, and the status
    bar while hovered or selected) until a link reaches it again."""
    from specto_core.gestures import delete_link

    doc = app.live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "Cam A", "layout": {"position": [380, 0]}}
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["."]},
        "summary": {"sentence": "The gallery always shows every image."},
    }
    app.engine.call("canvas.submit", {"document": doc, "label": "gallery"}, AGENT)
    spin(until=lambda: "kobi" in app.hosts.hosts)
    assert app.controller.views["kobi"]["health"] != "disconnected"

    delete_link(app.live, "l2")  # the user's gesture: one submit, allowed
    spin(30)
    info = app.controller.views["kobi"]
    assert info["health"] == "disconnected" and info["healthMessage"] == "not connected"
    app.controller.hoverView("kobi")
    assert app.controller.statusHint == (
        "kobi: not connected  ·  link it from the file tree or ask the agent"
    )
    app.controller.leaveView("kobi")
    # an agent submit that leaves kobi as it is goes through (the user made the orphan)
    doc = app.live.document()
    doc["views"]["kobi"]["label"] = "Front frames"
    app.engine.call("canvas.submit", {"document": doc, "label": "relabel"}, AGENT)
    # relinked: connected again
    doc = app.live.document()
    doc["links"]["l3"] = {
        "kind": "intaker",
        "scope": "selection",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {},
        "summary": {"sentence": "The gallery shows the files you pick."},
    }
    app.engine.call("canvas.submit", {"document": doc, "label": "relink"}, AGENT)
    spin(30)
    assert app.controller.views["kobi"]["health"] != "disconnected"


def test_the_agent_cannot_put_a_view_under_the_project_id(app):
    doc = app.live.document()
    doc["views"]["project"] = {"type": "gallery", "label": "nope"}
    with pytest.raises(Exception) as e:
        app.engine.call("canvas.submit", {"document": doc, "label": "try"}, AGENT)
    assert "project source" in str(e.value)


def test_the_overworld_shows_the_node_at_the_same_canvas_spot(app, spin):
    app.overworld.toggle()
    spin(until=lambda: app.overworld.active)
    node = find_item(app.window.contentItem(), "overProjectNode")
    assert node is not None and node.isVisible()
    assert (node.x(), node.y()) == PROJECT_NODE[:2] and node.width() == 52
    face = find_item(node, "identicon:projectNode")
    assert face.property("source").toString() == identicon_url("root", None)
    refresh = find_item(node, "refresh:project")
    assert refresh is None or not refresh.isVisible()  # refreshing is the normal view's
    b = app.overworld.bounds
    assert (b["x"], b["y"]) == (0.0, 0.0)
    app.overworld.toggle()


def test_the_wiring_layer_draws_the_wires_leaving_the_project_node(app, spin):
    """#82: the wiring layer draws every link; the project source's leave the node's
    right edge at the canvas origin (the file tree's intaker, l1)."""
    root = app.window.contentItem()
    app.controller.setWiring(True)
    spin(30)
    curve = find_item(root, "wire:l1")
    assert curve is not None and curve.isVisible()
    node = find_item(root, "projectNode")
    p = node.mapToScene(node.boundingRect().center())
    area = find_item(root, "canvasArea").mapToScene(QPointF(0, 0))
    # the curve starts at the node's right edge (area px: pad + canvas px - pan)
    x1, y1 = p.x() + node.width() / 2 - area.x(), p.y() - area.y()
    assert (curve.property("x1"), curve.property("y1")) == (x1, y1)
    assert find_item(root, "wireNode:l1").isVisible()
    app.controller.setWiring(False)
    spin(30)
    assert find_item(root, "wire:l1") is None
