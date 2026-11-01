"""Hover popups (a header button's tooltip, the file tree's thumbnail, the gallery's
preview) draw in the window's overlay, above every card: a neighbouring card that
overlaps the popup's spot never covers it, and a card's clip never cuts it."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from specto_core import CallContext, Project

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item
from specto_app.theme import COLORS

AGENT = CallContext(connection="mcp:1", origin="agent:s1")


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "demo")
    img = QImage(64, 48, QImage.Format.Format_RGB32)
    img.fill(QColor("#4080c0"))
    (proj.root / "pics").mkdir()
    assert img.save(str(proj.root / "pics" / "a.png"))
    return proj


def in_overlay(item) -> bool:
    p = item.parentItem()
    while p is not None:
        if p.metaObject().className().startswith("QQuickOverlay"):
            return True
        p = p.parentItem()
    return False


def hover(app, spin, name: str, dx: int = 0) -> None:
    item = find_item(app.window.contentItem(), name)
    assert item is not None, name
    p = item.mapToScene(item.boundingRect().center()).toPoint()
    QTest.mouseMove(app.window, QPoint(1, 1))
    spin(20)
    QTest.mouseMove(app.window, p + QPoint(dx, 0))
    spin(60)


def add_gallery(app, position: list, size=(640, 400)) -> None:
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": list(position), "size": list(size)},
    }
    pin(doc, "kobi")
    app.engine.call("canvas.submit", {"document": doc, "label": "add"}, AGENT)


def test_a_header_tooltip_draws_above_the_neighbouring_focused_card(app, spin):
    add_gallery(app, [360, 0])  # flush with the tree's right edge, where its tips reach
    spin(until=lambda: "kobi" in app.hosts.hosts)
    app.controller.focusView("kobi")  # the neighbour is the card on top
    spin(30)

    hover(app, spin, "close:fege")
    tip = find_item(app.window.contentItem(), "tip:close:fege")
    assert tip is not None and tip.isVisible()
    assert in_overlay(tip)
    r = tip.mapRectToScene(tip.boundingRect())
    assert r.right() > 12 + 360 + 10  # the tip reaches over the gallery card
    image = app.window.grabWindow()
    sample = image.pixelColor(int(r.right()) - 6, int(r.center().y()))
    assert sample.name() == COLORS["text"]  # the tip's own colour, not the card's surface

    hover(app, spin, "panArea")  # leaving the button hides the tip
    assert not tip.isVisible()


def test_the_file_tree_thumbnail_escapes_the_cards_clip_into_the_overlay(app, spin):
    spin(until=lambda: find_item(app.window.contentItem(), "treeRow:fege:pics/a.png") is not None)
    hover(app, spin, "treeRow:fege:pics/a.png")
    thumb_item = lambda: find_item(app.window.contentItem(), "fileTreeThumb:fege")  # noqa: E731
    spin(until=lambda: (thumb_item() or app.window).isVisible())
    thumb = thumb_item()
    assert thumb is not None and in_overlay(thumb)
    r = thumb.mapRectToScene(thumb.boundingRect())
    assert r.left() >= 12 + 280  # beside the tree, outside its card (the window has room)
    image = app.window.grabWindow()
    sample = image.pixelColor(int(r.center().x()), int(r.center().y()))
    assert sample.name() == "#4080c0"  # the picture itself, drawn over whatever is there
