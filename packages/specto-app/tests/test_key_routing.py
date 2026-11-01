"""Who owns Space and Home (#113). Space over the canvas is the canvas's: held, a drag
anywhere pans and no view hears the key; a tap with no drag goes to the focused view, so
a gallery's quick look, a video's play / pause and an image's step keep their Space.
Home and End go to the focused view first (image, video, pdf, usd, gallery jump to the
ends); Home no view takes pans the canvas home. Offscreen Qt, the real shell,
synthesised key and mouse events through the window."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from specto_core import CallContext, Project

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
NOMOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "demo")
    folder = proj.root / "front"
    folder.mkdir()
    for i in range(3):
        img = QImage(64, 48, QImage.Format.Format_RGB32)
        img.fill(QColor(40 * i, 90, 160))
        assert img.save(str(folder / f"{i:05d}.jpg"))
    return proj


def item(app, name: str):
    found = find_item(app.window.contentItem(), name)
    assert found is not None, name
    return found


def point(app, name: str, fx: float = 0.5, fy: float = 0.5) -> QPoint:
    it = item(app, name)
    r = it.boundingRect()
    return it.mapToScene(r.topLeft() + QPointF(r.width() * fx, r.height() * fy)).toPoint()


def add_view(app, spin, vid: str, vtype: str) -> None:
    doc = app.live.document()
    doc["views"][vid] = {
        "type": vtype,
        "label": vid.title(),
        "layout": {"position": [380, 40], "size": [480, 360]},
    }
    pin(doc, vid, sources=["front"])
    app.engine.call("canvas.submit", {"document": doc, "label": f"add {vid}"}, AGENT)
    spin(until=lambda: vid in app.hosts.hosts)


def focused_gallery(app, spin):
    """A gallery of three pictures, its second tile picked and its grid focused."""
    add_view(app, spin, "kobi", "gallery")
    host = app.hosts.hosts["kobi"]
    spin(until=lambda: host.gallery.count() == 3)
    grid = host.gallery.item
    grid.forceActiveFocus()
    grid.setProperty("selectedIndex", 1)
    spin(20)
    return host.item


def drag(app, spin, p0: QPoint, dx: int, dy: int) -> None:
    QTest.mousePress(app.window, LEFT, NOMOD, p0)
    for i in range(1, 5):
        QTest.mouseMove(app.window, p0 + QPoint(dx * i // 4, dy * i // 4))
        spin(5)
    QTest.mouseRelease(app.window, LEFT, NOMOD, p0 + QPoint(dx, dy))
    spin(10)


def test_space_and_a_drag_over_a_focused_gallery_pan_and_open_no_quick_look(app, spin):
    gallery = focused_gallery(app, spin)
    QTest.keyPress(app.window, Qt.Key.Key_Space)
    spin(10)
    assert app.controller.spaceHeld is True
    assert gallery.property("quickIndex") == -1  # the press alone opens nothing
    drag(app, spin, point(app, "slot:kobi"), -120, -40)
    QTest.keyRelease(app.window, Qt.Key.Key_Space)
    spin(20)
    assert app.controller.spaceHeld is False
    assert app.controller.pan == {"x": 120.0, "y": 40.0}
    assert gallery.property("quickIndex") == -1


def test_a_space_tap_over_a_focused_gallery_opens_its_quick_look_on_release(app, spin):
    gallery = focused_gallery(app, spin)
    QTest.keyPress(app.window, Qt.Key.Key_Space)
    spin(10)
    assert gallery.property("quickIndex") == -1
    QTest.keyRelease(app.window, Qt.Key.Key_Space)
    spin(20)
    assert gallery.property("quickIndex") == 1
    assert app.controller.pan == {"x": 0.0, "y": 0.0}
    QTest.keyClick(app.window, Qt.Key.Key_Space)  # a second tap closes it
    spin(20)
    assert gallery.property("quickIndex") == -1


def test_home_and_end_in_a_focused_image_go_to_the_ends_and_leave_the_pan(app, spin):
    add_view(app, spin, "zila", "image")
    spin(until=lambda: item(app, "position:zila").property("text") == "1 / 3")
    app.controller.setPan(100, 0)
    spin(10)
    app.hosts.hosts["zila"].item.forceActiveFocus()
    QTest.keyClick(app.window, Qt.Key.Key_End)
    spin(until=lambda: item(app, "position:zila").property("text") == "3 / 3")
    QTest.keyClick(app.window, Qt.Key.Key_Home)
    spin(until=lambda: item(app, "position:zila").property("text") == "1 / 3")
    assert app.controller.pan == {"x": 100.0, "y": 0.0}


def test_home_that_no_focused_view_takes_pans_the_canvas_home(app, spin):
    app.controller.setPan(400, 300)
    spin(10)
    app.controller.focusView("")
    item(app, "canvasArea").forceActiveFocus()
    QTest.keyClick(app.window, Qt.Key.Key_Home)
    spin(10)
    assert app.controller.pan == {"x": 0.0, "y": 0.0}
