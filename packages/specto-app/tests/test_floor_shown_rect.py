"""The tile floor's cues follow the rect a view is SHOWN at (#208): a floated view's
footprint sits at its window position, in screen space, so a pan leaves it put; a
collapsed view's footprint is its title bar; a floater's drop and an unfloat move it.
Seam 1 on ``FloorCues`` (a screen-space rect), seam 2 in the offscreen shell through
what QML reads (``uniforms``); the real-mouse flow is
tools/gui_flows/v2-208-floor-shown-rect.txt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item
from specto_app.canvas.floor import SELECT_W, FloorCues

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
NOMOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
PAD = 12  # the canvas area's padding: window px (0, 0) is the area's padded corner
BAR_H = 36.0  # a collapsed view's height: its title bar (#114)
VIEWPORT = (-12.0, -12.0, 1280.0, 742.0)


# -- seam 1: the cues take a rect in screen space ------------------------------------------------


def test_a_selection_in_screen_space_holds_its_footprint_there(qapp):
    c = FloorCues()
    c.select({"kobi": ((12, 12, 400, 300), "chain_0", "screen")}, 0.0)
    (e,) = c.effects(0.3)
    assert (e.group, e.space, e.geom) == ("tie", "screen", (12, 12, 412, 312))
    # a world-space entry still is one (the default)
    c.select({"kobi": ((40, 0, 400, 300), "chain_0")}, 1.0)
    (e,) = c.effects(1.3)
    assert (e.space, e.geom) == ("world", (40, 0, 440, 300))


def test_a_new_view_in_screen_space_is_on_screen_and_bands_there(qapp):
    c = FloorCues()
    # far in world px, but the rect is the window's own: a floater is always on screen
    assert c.view_created("pola", (9000, 300, 300, 200), "chain_0", VIEWPORT, 0.0, "screen") == "on"
    assert c.chip(0.0) is None
    effects = c.effects(0.1)
    assert effects and all(e.space == "screen" and e.group == "view" for e in effects)
    assert effects[0].geom == (9000, 300, 9300, 500)


# -- seam 2: the shell -----------------------------------------------------------------------------


def item(app, name: str):
    found = find_item(app.window.contentItem(), name)
    assert found is not None, name
    return found


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


def footprint(floor) -> tuple[tuple[float, float, float, float], str]:
    """The one selection footprint QML reads: its geometry and its space."""
    u = floor.uniforms
    ties = [i for i in range(8) if u[f"p{i}"].y() == SELECT_W and u[f"p{i}"].z() > 0]
    assert len(ties) == 1, ties
    (i,) = ties
    return u[f"g{i}"].toTuple(), ("world" if u[f"c{i}"].w() else "screen")


@pytest.fixture
def selected(app, spin):
    """kobi (a gallery at 300, 200, 400 x 300) added and selected: its footprint lit."""
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Kobi",
        "layout": {"position": [300, 200], "size": [400, 300]},
    }
    pin(doc, "kobi")
    app.engine.call("canvas.submit", {"document": doc, "label": "add"}, AGENT)
    spin(until=lambda: "kobi" in app.hosts.hosts)
    app.controller.selectView("kobi", False)
    spin(until=lambda: not app.tile_floor.ticking)
    assert footprint(app.tile_floor) == ((300, 200, 700, 500), "world")
    return app


def test_a_floated_view_s_footprint_sits_at_its_window_rect_and_stays_put_while_the_canvas_pans(
    selected, spin
):
    app, floor = selected, selected.tile_floor
    app.controller.toggleFloat("kobi")
    spin(until=lambda: app.controller.views["kobi"]["floating"])
    spin(until=lambda: not floor.ticking)
    # the same place on screen, now in the area's px: a pan changes no uniform
    assert footprint(floor) == ((PAD + 300, PAD + 200, PAD + 700, PAD + 500), "screen")
    assert floor.uniforms["worldLive"] is False
    app.controller.panBy(300, 150)
    spin(until=lambda: not floor.ticking)
    assert footprint(floor) == ((PAD + 300, PAD + 200, PAD + 700, PAD + 500), "screen")
    # Unfloat drops it on the canvas under it: the footprint follows, in the world again
    app.controller.toggleFloat("kobi")
    spin(until=lambda: not app.controller.views["kobi"]["floating"])
    spin(until=lambda: not floor.ticking)
    assert footprint(floor) == ((600, 350, 1000, 650), "world")


def test_a_floater_s_drop_moves_its_footprint(selected, spin):
    app, floor = selected, selected.tile_floor
    app.controller.toggleFloat("kobi")
    spin(until=lambda: app.controller.views["kobi"]["floating"])
    drag(app, spin, "header:kobi", 120, 60)
    spin(until=lambda: app.controller.views["kobi"]["fx"] == 420)
    spin(until=lambda: not floor.ticking)
    assert footprint(floor) == ((PAD + 420, PAD + 260, PAD + 820, PAD + 560), "screen")


def test_a_collapsed_view_s_footprint_is_its_title_bar(selected, spin):
    app, floor = selected, selected.tile_floor
    app.controller.toggleCollapse("kobi")
    spin(until=lambda: app.controller.views["kobi"]["collapsed"])
    spin(until=lambda: not floor.ticking)
    assert footprint(floor) == ((300, 200, 700, 200 + BAR_H), "world")
    app.controller.toggleCollapse("kobi")
    spin(until=lambda: not app.controller.views["kobi"]["collapsed"])
    spin(until=lambda: not floor.ticking)
    assert footprint(floor) == ((300, 200, 700, 500), "world")


def test_a_view_created_floating_bands_at_its_window_rect(app, spin):
    doc = app.live.document()
    doc["views"]["tamu"] = {
        "type": "gallery",
        "label": "Tamu",
        "layout": {
            "position": [9000, 100],
            "size": [400, 300],
            "floating": {"corner": "top_left", "offset": [40, 60], "by": "agent"},
        },
    }
    pin(doc, "tamu")
    app.engine.call("canvas.submit", {"document": doc, "label": "a floating gallery"}, AGENT)
    spin(200)
    floor = app.tile_floor
    assert floor.chip["show"] is False  # not far: it is in the window
    u = floor.uniforms
    lit = [i for i in range(8) if u[f"p{i}"].z() > 0]
    assert lit
    assert all(u[f"c{i}"].w() == 0 for i in lit)  # screen space
    assert u[f"g{lit[0]}"].toTuple() == (PAD + 40, PAD + 60, PAD + 440, PAD + 360)
