"""The float layer in the shell (#112, ADR 0021): the header's Float puts a view on the
float layer, fixed in the window above the pannable canvas; panning moves the canvas
under it and never pauses it; a window resize keeps its offset from its corner; its
header drag stays inside the window and snaps to the window edges and to other floating
views; Unfloat drops it on the canvas point under it. Offscreen Qt with synthesised
mouse input; the real-mouse flow is tools/gui_flows/v2-112-float-layer.txt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
NOMOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
PAD = 12  # the canvas area's padding: window px (0, 0) is the area's padded corner


def item(app, name: str):
    found = find_item(app.window.contentItem(), name)
    assert found is not None, name
    return found


def click(app, spin, name: str) -> None:
    it = item(app, name)
    r = it.boundingRect()
    QTest.mouseClick(app.window, LEFT, NOMOD, it.mapToScene(r.center()).toPoint())
    spin(10)


def drag(app, spin, name: str, dx: int, dy: int, fx: float = 0.3, fy: float = 0.5) -> None:
    it = item(app, name)
    r = it.boundingRect()
    p0 = it.mapToScene(r.topLeft() + QPoint(int(r.width() * fx), int(r.height() * fy))).toPoint()
    QTest.mousePress(app.window, LEFT, NOMOD, p0)
    for i in range(1, 5):
        QTest.mouseMove(app.window, p0 + QPoint(dx * i // 4, dy * i // 4))
        spin(5)
    QTest.mouseRelease(app.window, LEFT, NOMOD, p0 + QPoint(dx, dy))
    spin(10)


def card_pos(app, vid: str) -> tuple[float, float]:
    """A card's top-left in the canvas area's window px (its padded corner at 0, 0)."""
    card = item(app, f"view:{vid}")
    p = card.mapToItem(item(app, "canvasArea"), card.boundingRect().topLeft())
    return (p.x() - PAD, p.y() - PAD)


def add_views(app, views: dict[str, tuple[list, list]]) -> None:
    doc = app.live.document()
    for vid, (position, size) in views.items():
        doc["views"][vid] = {
            "type": "gallery",
            "label": vid.title(),
            "layout": {"position": list(position), "size": list(size)},
        }
        pin(doc, vid)
    app.engine.call("canvas.submit", {"document": doc, "label": "add"}, AGENT)


@pytest.fixture
def floated(app, spin):
    """kobi (a gallery) floated from 420, 460 in the 1600 x 900 window: its nearest
    corner is the bottom-left, 100 px above the bottom edge."""
    add_views(app, {"kobi": ([420, 460], [600, 340]), "tamu": ([1100, 40], [400, 300])})
    spin(until=lambda: "kobi" in app.hosts.hosts)
    click(app, spin, "float:kobi")
    spin(until=lambda: app.live.data["views"]["kobi"]["layout"].get("floating"))
    return app


def test_float_keeps_the_view_in_place_and_panning_moves_the_canvas_under_it(floated, spin):
    app = floated
    floating = app.live.data["views"]["kobi"]["layout"]["floating"]
    assert floating == {"corner": "bottom_left", "offset": [420.0, 100.0], "by": "user"}
    rec = app.live.history[-1]
    assert (rec.origin, rec.label) == ("user", "float Kobi")
    assert card_pos(app, "kobi") == (420.0, 460.0)
    assert app.controller.views["kobi"]["floating"] is True

    app.controller.panBy(3000, 2000)  # every canvas view is off-screen now
    spin(until=lambda: app.controller.views["tamu"]["paused"])
    assert card_pos(app, "kobi") == (420.0, 460.0)
    assert app.controller.views["kobi"]["paused"] is False
    assert item(app, "view:kobi").isVisible()
    # the floater is drawn above every canvas view
    assert item(app, "view:kobi").z() > item(app, "view:tamu").z()


def test_a_window_resize_keeps_the_floater_at_its_corner_offset(floated, spin):
    app = floated
    area = item(app, "canvasArea")
    app.window.resize(1300, 700)
    spin(until=lambda: app.live.data["window_frame"]["rect"]["w"] != 1600, timeout=3)
    wh = area.height() - 2 * PAD
    x, y = card_pos(app, "kobi")
    # still 420 px from the left edge and 100 px above the bottom edge
    assert (x, wh - (y + 340)) == (420.0, 100.0)
    assert app.live.data["views"]["kobi"]["layout"]["floating"]["offset"] == [420.0, 100.0]


def test_dragging_a_floater_snaps_to_the_window_edge_and_other_floaters_only(floated, spin):
    app = floated
    # 6 px short of the window's left edge and 4 px short of tamu's top (a canvas view
    # at y 40): the window edge pulls, the canvas view does not
    drag(app, spin, "header:kobi", -414, -416)
    floating = app.live.data["views"]["kobi"]["layout"]["floating"]
    assert floating == {"corner": "top_left", "offset": [0.0, 44.0], "by": "user"}
    rec = app.live.history[-1]
    assert (rec.origin, rec.label) == ("user", "move Kobi")
    assert card_pos(app, "kobi") == (0.0, 44.0)

    # float tamu too: kobi now snaps to its border, still inside the window
    click(app, spin, "float:tamu")
    spin(until=lambda: app.live.data["views"]["tamu"]["layout"].get("floating"))
    tx, ty = card_pos(app, "tamu")
    # 7 px past tamu's left edge and 4 px below its top: both of its borders pull
    drag(app, spin, "header:kobi", int(tx - 600 + 7), 0)
    assert card_pos(app, "kobi") == (tx - 600, ty)

    # dragged far past the window's edge, it stays inside the window
    drag(app, spin, "header:kobi", -900, 1200)
    x, y = card_pos(app, "kobi")
    assert x == 0.0 and y == item(app, "canvasArea").height() - 2 * PAD - 340


def test_unfloat_drops_the_view_on_the_canvas_under_it_after_a_pan(floated, spin):
    app = floated
    app.controller.panBy(700, 300)
    spin(5)
    click(app, spin, "float:kobi")
    spin(until=lambda: not app.live.data["views"]["kobi"]["layout"].get("floating"))
    # window position (420, 460) at the pan (700, 300)
    assert app.live.data["views"]["kobi"]["layout"]["position"] == [1120.0, 760.0]
    assert card_pos(app, "kobi") == (420.0, 460.0)
    rec = app.live.history[-1]
    assert (rec.origin, rec.label) == ("user", "unfloat Kobi")
    app.controller.panBy(-700, -300)
    spin(5)
    assert card_pos(app, "kobi") == (1120.0, 760.0)


def test_a_wire_from_a_floater_to_a_canvas_view_follows_the_pan(app, spin):
    add_views(app, {"kobi": ([420, 460], [600, 340]), "tamu": ([1100, 40], [400, 300])})
    doc = app.live.document()
    doc["links"]["l9"] = {
        "kind": "follow",
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "tamu", "port": "selection"},
        "params": {"match": None},
        "summary": {"sentence": "The rear gallery follows the front one's picks."},
    }
    app.engine.call("canvas.submit", {"document": doc, "label": "link"}, AGENT)
    spin(until=lambda: "kobi" in app.hosts.hosts)
    click(app, spin, "float:kobi")
    spin(until=lambda: app.live.data["views"]["kobi"]["layout"].get("floating"))
    before = app.controller.wireMap["l9"]

    app.controller.panBy(200, 100)
    after = app.controller.wireMap["l9"]
    # the source end rides the float layer (fixed on screen: it moves with the pan in
    # canvas px), the target end stays on the canvas
    assert (after["x1"] - before["x1"], after["y1"] - before["y1"]) == (200.0, 100.0)
    assert (after["x2"], after["y2"]) == (before["x2"], before["y2"])


def test_the_overworld_shows_floating_views_in_a_strip_along_the_top(floated, spin):
    app = floated
    ow = app.overworld
    ow.toggle()
    spin(until=lambda: ow.active)

    assert [c["id"] for c in ow.floatingCards] == ["kobi"]
    assert ow.floatingCards[0]["hasSnapshot"]  # grabbed on the way in, like any view
    strip = item(app, "overFloatStrip")
    assert strip.isVisible() and strip.mapToScene(strip.boundingRect().topLeft()).y() < 120
    assert item(app, "overFloat:kobi").isVisible()
    assert item(app, "overFloatTitle:kobi").property("text") == "kobi: Kobi"
    # not a card on the canvas: the floater is not on the canvas; a faint outline marks
    # where it floats over the window, so its wires still land
    assert not item(app, "overCard:kobi").isVisible()
    assert item(app, "overCard:tamu").isVisible()
    assert item(app, "overFloatGhost:kobi").isVisible()

    # a click on its chip goes back to the normal view, the floater flashing
    flashed = []
    app.controller.flash.connect(flashed.append)
    click(app, spin, "overFloat:kobi")
    spin(until=lambda: not ow.active)
    assert flashed == ["kobi"]

    # nothing floats: no strip
    click(app, spin, "float:kobi")
    ow.toggle()
    spin(until=lambda: ow.active)
    assert ow.floatingCards == [] and not item(app, "overFloatStrip").isVisible()


def test_the_status_bar_says_a_hovered_view_floats_and_the_pointer_finds_it_on_top(floated, spin):
    app = floated
    # pan so that tamu, on the canvas, lies under the floater: the floater wins
    app.controller.panBy(800, -440)
    spin(5)
    p = item(app, "view:kobi")
    at = p.mapToScene(p.boundingRect().center())
    app.controller.hoverAt(at.x(), at.y())
    assert app.controller.hoveredView == "kobi"
    assert "floating: fixed in the window" in app.controller.statusHint
