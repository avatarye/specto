"""Seam 2 (#94): Ctrl+wheel zooms the pdf view as one document, anchored at the pointer.
``PdfView.qml`` standing alone in a shown offscreen window over a pdf whose second page
carries two dots (red near its top-left corner, blue further in). Paged to that page
with PageDown, a Ctrl+wheel notch with the pointer on the red dot must leave the red
dot under the pointer, and the blue dot must move away from it by the zoom factor: the
whole document scales about the pointer, not each page about its own centre.

Before the fix the zoom went through the page navigator, which keeps the current page's
top in place and lets every page grow about its own centre (PdfMultiPageView centres
each page in its row), so the dot under the pointer slid away.

The fit width the view opens at and keeps through a resize (also one made while the
overworld shows) is checked the same way, on the running shell."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QMarginsF, QPoint, QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QWheelEvent
from PySide6.QtTest import QTest

from specto_app.views.pdf_view import PDF_QML

pytest.importorskip("PySide6.QtPdf")

RED_PT = (40.0, 40.0)  # page points, from the page's top-left corner
BLUE_PT = (260.0, 300.0)
A5_WIDTH_PT = 420.0


def dotted_pdf(path: Path, pages: int = 3, dotted: tuple[int, ...] = (1,)) -> Path:
    """White A5 pages; the ``dotted`` ones (the second) have a red and a blue dot (radius
    4 pt)."""
    w = QPdfWriter(str(path))
    w.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A5), QPageLayout.Orientation.Portrait, QMarginsF()
        )
    )
    w.setResolution(72)
    p = QPainter(w)
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(pages):
        if i:
            w.newPage()
        if i not in dotted:
            continue
        for (x, y), colour in ((RED_PT, "#ff0000"), (BLUE_PT, "#0000ff")):
            p.setBrush(QColor(colour))
            p.drawEllipse(QRectF(x - 4, y - 4, 8, 8))
    p.end()
    return path


def centroid(image: QImage, colour: str) -> tuple[float, float] | None:
    """The mean position of the pixels close to ``colour`` (window pixels), or None."""
    img = image.convertToFormat(QImage.Format.Format_RGB32)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
    b, g, r = (a[:, : img.width(), k].astype(int) for k in range(3))
    want = {"red": (r > 200) & (g < 70) & (b < 70), "blue": (b > 200) & (r < 70) & (g < 70)}[colour]
    ys, xs = np.nonzero(want)
    if len(xs) < 4:
        return None
    k = img.width() / image.width() if image.width() else 1.0
    return float(xs.mean()) / k, float(ys.mean()) / k


@pytest.fixture
def pdf_view(qapp, spin, tmp_path: Path):
    from PySide6.QtQuick import QQuickView

    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(640, 560)
    view.setSource(QUrl.fromLocalFile(str(PDF_QML)))
    root = view.rootObject()
    assert root is not None, view.errors()
    view.show()
    root.setProperty("source", QUrl.fromLocalFile(str(dotted_pdf(tmp_path / "dots.pdf"))))
    spin(until=lambda: root.property("status") == "ready")
    yield view, root
    view.close()
    view.deleteLater()
    spin(20)


def dots(view, spin) -> dict[str, tuple[float, float] | None]:
    spin(250)  # pages render off the GUI thread
    image = view.grabWindow()
    return {c: centroid(image, c) for c in ("red", "blue")}


def wheel(view, at: QPointF, notches: int, mods=Qt.KeyboardModifier.ControlModifier) -> None:
    ev = QWheelEvent(
        at,
        QPointF(view.mapToGlobal(at.toPoint())),
        QPoint(),
        QPoint(0, 120 * notches),
        Qt.MouseButton.NoButton,
        mods,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QCoreApplication.sendEvent(view, ev)


def test_ctrl_wheel_zooms_the_document_about_the_pointer(pdf_view, spin):
    view, root = pdf_view
    # PageDown brings the second page (and its corner dot) to the top of the view
    QTest.keyClick(view, Qt.Key.Key_PageDown)
    before, prev = dots(view, spin), None
    while before != prev:  # the page step is animated
        before, prev = dots(view, spin), before
    assert before["red"] is not None and before["blue"] is not None, before
    red = QPointF(*before["red"])
    fit = root.property("zoom")
    for notches in (1, 2, -1, -2):
        z0 = root.property("zoom")
        prev = dots(view, spin)
        wheel(view, red, notches)
        now = dots(view, spin)
        z1 = root.property("zoom")
        assert z1 == pytest.approx(z0 * 1.2**notches, rel=1e-3)
        assert root.property("fit") == "none"
        # the point under the pointer stays put
        assert now["red"] is not None, (notches, now)
        assert abs(now["red"][0] - red.x()) <= 2 and abs(now["red"][1] - red.y()) <= 2, (
            notches,
            now["red"],
            (red.x(), red.y()),
        )
        # one document zoom: the rest of the page scales about the pointer
        k = z1 / z0
        if prev["blue"] is None:
            continue  # out of view before this notch
        want = (
            red.x() + (prev["blue"][0] - red.x()) * k,
            red.y() + (prev["blue"][1] - red.y()) * k,
        )
        if not (10 < want[0] < view.width() - 30 and 10 < want[1] < view.height() - 40):
            continue  # zoomed past the view (scroll bar and footer excluded)
        assert now["blue"] is not None, (notches, now)
        assert abs(now["blue"][0] - want[0]) <= 3 and abs(now["blue"][1] - want[1]) <= 3, (
            notches,
            now["blue"],
            want,
        )
    assert root.property("zoom") == pytest.approx(fit, rel=1e-3)
    # narrower than the view, a page is centred in it: the zoom still keeps the pointer's
    # line, the page shrinks towards the middle
    wheel(view, red, -1)
    now = dots(view, spin)
    assert abs(now["red"][1] - red.y()) <= 2, (now["red"], red.y())
    assert red.x() < now["red"][0] < view.width() / 2


def test_a_pdf_view_opens_fitted_and_stays_fitted_after_a_resize_in_the_overworld(app, spin):
    """A pinned intaker hands the host its pdf at once; the view opens fitted to its
    width (it opened at 100 %: PdfMultiPageView resets its scale when the source
    changes, after the fit). Resized while the overworld shows (hosts paused, the slot
    hidden), it comes back as a view resized in plain sight does: fitted, the page
    where it would be."""
    from specto_app.app import find_item
    from specto_core import CallContext

    agent = CallContext(connection="mcp:1", origin="agent:s1")
    root = Path(app.project.root)
    (root / "papers").mkdir()
    dotted_pdf(root / "papers" / "report.pdf", 3, dotted=(0, 1, 2))
    app.engine.links.resolve("l1")
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "pdf",
        "label": "Report",
        "layout": {"position": [240, 0], "size": [330, 400]},
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["papers"], "screener": ["@pdf"]},
        "summary": {"sentence": "The pdf view shows the report."},
    }
    app.engine.call("canvas.submit", {"document": doc, "label": "a pdf view"}, agent)
    spin(until=lambda: "kobi" in app.hosts.hosts)
    item = app.hosts.hosts["kobi"].item
    spin(until=lambda: item.property("status") == "ready")
    slot = find_item(app.window.contentItem(), "slot:kobi")

    def red_dot() -> tuple[float, float]:
        """The page's red dot relative to the view, once the view is fitted."""
        spin(400)  # pages render off the GUI thread
        assert item.property("fit") == "width"
        assert item.property("zoom") == pytest.approx((item.width() - 18) / A5_WIDTH_PT, rel=1e-3)
        corner = slot.mapToScene(slot.boundingRect().topLeft())
        red = centroid(app.window.grabWindow(), "red")
        assert red is not None
        return red[0] - corner.x(), red[1] - corner.y()

    def resize(w: int) -> None:
        doc = app.live.document()
        doc["views"]["kobi"]["layout"]["size"] = [w, 400]
        app.engine.call("canvas.submit", {"document": doc, "label": f"{w} wide"}, agent)
        spin(60)

    red_dot()
    resize(560)
    in_sight = red_dot()
    resize(330)
    app.controller.set_all_paused(True)  # what the overworld does
    spin(60)
    resize(560)
    app.controller.set_all_paused(False)
    after = red_dot()
    assert abs(after[0] - in_sight[0]) <= 1 and abs(after[1] - in_sight[1]) <= 1, (after, in_sight)
