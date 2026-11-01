"""Seam 2 (#185): dragging out of the image view, offscreen. Ctrl+drag carries the shown
image (its file); Shift+drag marks a crop in image pixels, and a drag from inside it
carries that part as a PNG; a plain drag still pans, a click outside the crop or Esc
clears it."""

from __future__ import annotations

from pathlib import Path

import pytest
from app_doc_helpers import pin
from drag_helpers import DropTarget, drags_into
from PySide6.QtCore import QPoint, QPointF, QSizeF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.app import find_item
from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.views.image_view import ImageFactory, ImageHost
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager

LEFT = Qt.MouseButton.LeftButton
NONE, SHIFT, CTRL = (
    Qt.KeyboardModifier.NoModifier,
    Qt.KeyboardModifier.ShiftModifier,
    Qt.KeyboardModifier.ControlModifier,
)


@pytest.fixture
def window(qapp, spin):
    from PySide6.QtQuick import QQuickView

    from specto_app.qt_setup import install_qml

    view = QQuickView()
    view.resize(480, 360)
    view._theme = install_qml(view.engine())
    view.show()
    yield view
    view.close()
    view.deleteLater()
    spin(20)


@pytest.fixture
def target(qapp):
    t = DropTarget()
    yield t
    t.close()


@pytest.fixture
def shown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, window, spin):
    """An image view showing a 200x100 PNG: red on the left half, blue on the right."""
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    proj = Project.init(tmp_path / "proj", "pictures")
    (proj.root / "imgs").mkdir()
    img = QImage(200, 100, QImage.Format.Format_RGB32)
    img.fill(QColor(220, 30, 30))
    for x in range(100, 200):
        for y in range(100):
            img.setPixelColor(x, y, QColor(30, 30, 220))
    path = proj.root / "imgs" / "halves.png"
    assert img.save(str(path))
    engine = Engine(project=proj, jobs=InlineJobManager())
    engine.links.resolve("l1")
    clear_pending()
    reg = HostRegistry(HostContext(engine, window.engine()), load_entry_points=False)
    reg.add_factory(ImageFactory())
    doc = engine.live.document()
    doc["views"]["tamu"] = {"type": "image", "label": "Pictures", "layout": {}}
    pin(doc, "tamu", sources=["imgs"])
    engine.live.submit(doc, origin="agent:s1", label="image")
    host = reg.host("tamu")
    assert isinstance(host, ImageHost)
    host.item.setParentItem(window.contentItem())
    host.item.setSize(QSizeF(480, 360))
    spin(until=lambda: host.ctx.health_of("tamu")["status"] == "ok")
    spin(30)
    return host, path


def at(host: ImageHost, px: float, py: float) -> QPoint:
    """The window point over image pixel (px, py)."""
    img = find_item(host.item, "image")
    zoom = float(host.item.property("zoom"))
    p = QPointF(img.x() + px * zoom, img.y() + py * zoom)
    return host.item.mapToScene(p).toPoint()


def drag(window, a: QPoint, b: QPoint, mods=NONE, steps: int = 8) -> None:
    QTest.mousePress(window, LEFT, mods, a)
    for i in range(1, steps + 1):
        QTest.mouseMove(window, a + (b - a) * i / steps)
    QTest.mouseRelease(window, LEFT, mods, b)


def crop_of(host: ImageHost) -> list[float]:
    r = host.item.property("crop")
    return [r.x(), r.y(), r.width(), r.height()]


def test_ctrl_drag_carries_the_shown_image_file(shown, window, target, spin):
    host, path = shown
    with drags_into(target) as drags:
        drag(window, at(host, 50, 50), at(host, 50, 50) + QPoint(80, 0), CTRL)
        spin(20)
    assert len(drags) == 1
    drop = target.drops[0]
    assert drop["urls"] == [path.as_uri()] and drop["text"] == str(path)
    assert drop["png"] == 0
    (item,) = target.specto()["items"]
    assert (item["view"], item["type"], item["kind"], item["path"]) == (
        "tamu",
        "image",
        "image",
        "imgs/halves.png",
    )
    assert item["label"] == "halves.png"


def test_shift_drag_marks_a_crop_and_a_drag_from_inside_carries_it_as_a_png(
    shown, window, target, spin
):
    host, _path = shown
    with drags_into(target) as drags:
        drag(window, at(host, 110, 10), at(host, 190, 90), SHIFT)
        spin(20)
        x, y, w, h = crop_of(host)
        assert drags == []  # marking is no drag
        assert 105 <= x <= 112 and 5 <= y <= 12 and 185 <= x + w <= 195 and 85 <= y + h <= 95
        assert find_item(host.item, "crop:tamu").isVisible()
        drag(window, at(host, 150, 50), at(host, 150, 50) + QPoint(0, 60))
        spin(20)
    assert len(drags) == 1
    drop = target.drops[0]
    assert drop["urls"] == []  # the picture is the thing dragged, not the file
    assert drop["png"] > 0
    (item,) = target.specto()["items"]
    assert item["kind"] == "image" and item["path"] == "imgs/halves.png"
    assert item["meta"] == {
        "crop": [int(x), int(y), int(w), int(h)],
        "size": [200, 100],
        "rendered": True,
    }
    png = QImage.fromData(bytes(drags[0].mimeData().data("image/png").data()), "PNG")
    assert (png.width(), png.height()) == (int(w), int(h))
    assert png.pixelColor(png.width() // 2, png.height() // 2) == QColor(30, 30, 220)
    assert crop_of(host) == [x, y, w, h]  # the crop stays after its drag


def test_a_plain_drag_still_pans_and_a_click_outside_or_esc_clears_the_crop(
    shown, window, target, spin
):
    host, _ = shown
    window.requestActivate()
    host.item.forceActiveFocus()
    spin(until=host.item.hasActiveFocus)
    for _ in range(3):  # zoomed in past the view's width: pannable
        QTest.keyClick(window, Qt.Key.Key_Plus)
    spin(20)
    img = find_item(host.item, "image")
    with drags_into(target) as drags:
        before = img.x()
        drag(window, at(host, 100, 50), at(host, 100, 50) + QPoint(-60, 0))
        spin(20)
        assert img.x() == pytest.approx(before - 60, abs=2)
        drag(window, at(host, 90, 40), at(host, 110, 60), SHIFT)
        spin(20)
        assert crop_of(host)[2] > 0
        QTest.mouseClick(window, LEFT, NONE, at(host, 130, 50))  # outside the crop
        spin(20)
        assert crop_of(host)[2] == 0
        drag(window, at(host, 90, 40), at(host, 110, 60), SHIFT)
        spin(20)
        assert crop_of(host)[2] > 0
        QTest.keyClick(window, Qt.Key.Key_Escape)
        spin(20)
        assert crop_of(host)[2] == 0
    assert drags == [] and target.drops == []


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """For the shell (conftest's ``app``): one 200x100 PNG under imgs/."""
    proj = Project.init(tmp_path / "proj", "pictures")
    (proj.root / "imgs").mkdir()
    img = QImage(200, 100, QImage.Format.Format_RGB32)
    img.fill(QColor(220, 30, 30))
    assert img.save(str(proj.root / "imgs" / "halves.png"))
    return proj


def test_in_the_shell_a_click_then_esc_clears_the_crop(app, spin):
    """The shell's window-wide Escape shortcut (drop the focus and the selection) must not
    take Esc while the image view shows a crop: the view claims it (seam 3 drag-out
    flow, #185)."""
    doc = app.live.document()
    doc["views"]["tamu"] = {
        "type": "image",
        "label": "Pictures",
        "layout": {"position": [420, 0], "size": [480, 360]},
    }
    pin(doc, "tamu", sources=["imgs"])
    app.live.submit(doc, origin="agent:s1", label="image")
    spin(until=lambda: "tamu" in app.hosts.hosts, timeout=10)
    host = app.hosts.hosts["tamu"]
    spin(until=lambda: host.ctx.health_of("tamu")["status"] == "ok", timeout=10)
    spin(60)
    drag(app.window, at(host, 40, 20), at(host, 120, 80), SHIFT)
    spin(30)
    assert crop_of(host)[2] > 0
    QTest.mouseClick(app.window, LEFT, NONE, at(host, 80, 50))  # inside the crop: kept
    spin(30)
    assert crop_of(host)[2] > 0
    QTest.keyClick(app.window, Qt.Key.Key_Escape)
    spin(30)
    assert crop_of(host)[2] == 0
