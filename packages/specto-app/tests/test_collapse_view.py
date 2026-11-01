"""Collapsed views in the shell (#114): the header's collapse button shrinks a view to its
title bar at the same width, one user submit; its content pauses while its links keep
delivering, its ports stay on the title bar, snapping and the marquee use the collapsed
rect, and expanding restores the stored height. The health dot shows only when the view
is not healthy. Offscreen Qt with synthesised mouse input; the real-mouse flow is
tools/gui_flows/v2-114-collapse.txt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
NOMOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
#: a collapsed view's height: the 30 px header, the frame's 1 px inset and the 2 px
#: gutter above and below it
BAR_H = 36.0
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


def submit(app, doc: dict, label: str) -> dict:
    return app.engine.call("canvas.submit", {"document": doc, "label": label}, AGENT)


def add_views(app, views: dict[str, tuple[list, list]]) -> None:
    doc = app.live.document()
    for vid, (position, size) in views.items():
        doc["views"][vid] = {
            "type": "gallery",
            "label": vid.title(),
            "layout": {"position": list(position), "size": list(size)},
        }
        pin(doc, vid)
    submit(app, doc, "add")


def card_pos(app, vid: str) -> tuple[float, float]:
    """A card's top-left in the canvas area's window px (its padded corner at 0, 0)."""
    card = item(app, f"view:{vid}")
    p = card.mapToItem(item(app, "canvasArea"), card.boundingRect().topLeft())
    return (p.x() - PAD, p.y() - PAD)


def collapsed(app, vid: str) -> bool:
    return bool(app.live.data["views"][vid]["layout"].get("collapsed"))


@pytest.fixture
def two(app, spin):
    """kobi (600 x 340 at 420, 460) and tamu (400 x 300 at 1100, 40), both galleries."""
    add_views(app, {"kobi": ([420, 460], [600, 340]), "tamu": ([1100, 40], [400, 300])})
    spin(until=lambda: "kobi" in app.hosts.hosts and "tamu" in app.hosts.hosts)
    return app


def test_the_collapse_button_shows_the_title_bar_alone_and_pauses_the_content(two, spin):
    app = two
    click(app, spin, "collapse:kobi")
    spin(until=lambda: collapsed(app, "kobi"))

    rec = app.live.history[-1]
    assert (rec.origin, rec.label) == ("user", "collapse Kobi")
    view = item(app, "view:kobi")
    spin(until=lambda: view.height() == BAR_H)
    assert view.width() == 600 and app.controller.rect_of("kobi") == (420.0, 460.0, 600.0, BAR_H)
    assert not item(app, "slot:kobi").isVisible()
    assert item(app, "title:kobi").isVisible()
    # paused like a culled view, and the status bar says why and what to do
    info = app.controller.views["kobi"]
    assert info["paused"] is True and info["collapsed"] is True
    app.controller.hoverView("kobi")
    assert app.controller.statusHint == "kobi: collapsed · expand"

    click(app, spin, "collapse:kobi")
    spin(until=lambda: not collapsed(app, "kobi"))
    assert app.live.history[-1].label == "expand Kobi"
    spin(until=lambda: view.height() == 340)
    assert app.live.data["views"]["kobi"]["layout"]["size"] == [600.0, 340.0]
    spin(until=lambda: not app.controller.views["kobi"]["paused"])
    assert item(app, "slot:kobi").isVisible()


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


def test_snapping_and_the_marquee_use_the_collapsed_rect(two, spin):
    app = two
    click(app, spin, "collapse:kobi")
    spin(until=lambda: item(app, "view:kobi").height() == BAR_H)

    # tamu dragged so its top lands 5 px below kobi's title bar (at 460 + 36) and its
    # left 6 px right of kobi's: both borders pull, though the expanded kobi would
    # cover the spot
    drag(app, spin, "header:tamu", 426 - 1100, 501 - 40)
    assert app.live.data["views"]["tamu"]["layout"]["position"] == [420.0, 496.0]

    # a marquee below the bar, inside the stored rect, does not pick kobi
    app.controller.selectInRect(430, 700, 50, 50, [])
    assert app.controller.selectedViews == ["tamu"]
    app.controller.selectInRect(430, 470, 50, 10, [])
    assert app.controller.selectedViews == ["kobi"]


def test_a_collapsed_view_resizes_its_width_only_and_expands_to_its_stored_height(two, spin):
    app = two
    click(app, spin, "collapse:kobi")
    spin(until=lambda: item(app, "view:kobi").height() == BAR_H)
    assert not item(app, "resize:kobi:bottom").isVisible()
    assert not item(app, "resize:kobi:bottom-right").isVisible()

    drag(app, spin, "resize:kobi:right", -100, 0, fx=0.5)
    assert app.live.data["views"]["kobi"]["layout"]["size"] == [500.0, 340.0]
    assert app.live.history[-1].label == "resize Kobi"
    click(app, spin, "collapse:kobi")
    spin(until=lambda: item(app, "view:kobi").height() == 340)


def test_ports_sit_on_the_title_bar_and_wires_land_there(two, spin):
    app = two
    doc = app.live.document()
    doc["links"]["l9"] = {
        "kind": "follow",
        "source": {"view": "tamu", "port": "selection"},
        "target": {"view": "kobi", "port": "selection"},
        "params": {"match": None},
        "summary": {"sentence": "The front gallery follows the rear one's picks."},
    }
    submit(app, doc, "link")
    click(app, spin, "collapse:kobi")
    spin(until=lambda: item(app, "view:kobi").height() == BAR_H)

    # kobi has two inputs (its pin and l9): both circles on the bar's middle row, the
    # second one port_px + port_stack further right, inside the bar (a left neighbour
    # snapped beside it keeps its output circle clear), the title moved past it
    view = item(app, "view:kobi")
    mid = view.mapToScene(view.boundingRect().topLeft()).y() + BAR_H / 2
    centres = []
    for name in ("port:kobi:in", "port:kobi:in:1", "port:kobi:out"):
        c = item(app, name)
        centres.append(c.mapToScene(c.boundingRect().center()))
    assert [p.y() for p in centres] == [mid, mid, mid]
    assert centres[1].x() - centres[0].x() == 24
    title = item(app, "title:kobi")
    assert title.mapToScene(title.boundingRect().topLeft()).x() > centres[1].x() + 10
    wire = app.controller.wireMap["l9"]
    assert (wire["x2"], wire["y2"]) == (420 + 2 + 24, 460 + BAR_H / 2)


def pictures(root, folder: str, names: list[str]) -> None:
    (root / folder).mkdir()
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(QColor("#4080c0"))
    for name in names:
        assert img.save(str(root / folder / name))


def test_a_link_keeps_delivering_into_a_collapsed_view_and_expanding_shows_it(app, spin):
    pictures(app.project.root, "one", ["a.png"])
    pictures(app.project.root, "two", ["b.png", "c.png"])
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Pics",
        "layout": {"position": [420, 40], "size": [600, 340]},
    }
    lid = pin(doc, "kobi", sources=["one"])
    submit(app, doc, "add")
    host = lambda: app.hosts.hosts.get("kobi")  # noqa: E731
    spin(until=lambda: host() is not None and host().collection is not None)
    assert len(host().collection.items) == 1

    click(app, spin, "collapse:kobi")
    spin(until=lambda: app.controller.views["kobi"]["paused"])
    # the agent points the link at other pictures while kobi is collapsed: they arrive
    doc = app.live.document()
    doc["links"][lid]["params"]["sources"] = ["two"]
    submit(app, doc, "show the other pictures")
    spin(until=lambda: host().collection is not None and len(host().collection.items) == 2)
    assert app.controller.views["kobi"]["paused"] is True

    click(app, spin, "collapse:kobi")
    spin(until=lambda: not app.controller.views["kobi"]["paused"])
    assert app.controller.views["kobi"]["health"] == "ok"
    assert len(host().collection.items) == 2 and item(app, "slot:kobi").isVisible()


def test_the_health_dot_shows_only_when_the_view_is_not_healthy(two, spin):
    app = two
    ctx = app.hosts.ctx
    ctx.set_health("kobi", "ok", "")
    spin(until=lambda: app.controller.views["kobi"]["health"] == "ok")
    dot = item(app, "health:kobi")
    assert not dot.isVisible()
    for status in ("loading", "error", "approval", "disconnected", "paused"):
        ctx.set_health("kobi", status, "why")
        spin(until=lambda s=status: app.controller.views["kobi"]["health"] == s)
        assert dot.isVisible(), status
    ctx.set_health("kobi", "ok", "")
    spin(until=lambda: not dot.isVisible())


def test_the_overworld_shows_a_collapsed_view_as_its_title_bar(two, spin):
    app = two
    click(app, spin, "collapse:kobi")
    spin(until=lambda: app.controller.views["kobi"]["paused"])
    ow = app.overworld
    ow.toggle()
    spin(until=lambda: ow.active)

    card = ow.cardMap["kobi"]
    assert card["collapsed"] is True
    assert card["rect"] == {"x": 420.0, "y": 460.0, "w": 600.0, "h": BAR_H}
    assert item(app, "overCollapsed:kobi").isVisible()
    assert not item(app, "overSnapshot:kobi").isVisible()
    assert item(app, "overTitle:kobi").isVisible()
    assert not ow.cardMap["tamu"]["collapsed"]
    # a resize there keeps the height it expands to, as in the normal view
    assert ow.dragResize("kobi", 420, 460, 500, BAR_H, ["right"])["h"] == BAR_H
    assert ow.endResize("kobi", 420, 460, 500, BAR_H, ["right"])
    assert app.live.data["views"]["kobi"]["layout"]["size"] == [500.0, 340.0]

    ow.toggle()
    spin(until=lambda: not ow.active)
    assert app.controller.views["kobi"]["paused"] is True  # still collapsed
    assert app.controller.views["tamu"]["paused"] is False


def test_a_collapsed_floater_drops_where_its_title_bar_is_near_the_window_bottom(two, spin):
    """#116: a collapsed floating view is anchored and kept inside the window by its
    title bar, not its stored height: dropped near the bottom edge it stays there."""
    app = two
    click(app, spin, "collapse:kobi")
    spin(until=lambda: item(app, "view:kobi").height() == BAR_H)
    click(app, spin, "float:kobi")
    spin(until=lambda: app.live.data["views"]["kobi"]["layout"].get("floating"))
    assert card_pos(app, "kobi") == (420.0, 460.0)

    # down 380 px: the bar's bottom 24 px above the window's (900), clear of its snap
    drag(app, spin, "header:kobi", 0, 380)
    floating = app.live.data["views"]["kobi"]["layout"]["floating"]
    assert floating == {"corner": "bottom_left", "offset": [420.0, 24.0], "by": "user"}
    assert card_pos(app, "kobi") == (420.0, 840.0)

    # expanded, the view grows up from the bar's bottom edge, its stored height back
    click(app, spin, "collapse:kobi")
    spin(until=lambda: item(app, "view:kobi").height() == 340)
    assert card_pos(app, "kobi") == (420.0, 900.0 - 24.0 - 340.0)
