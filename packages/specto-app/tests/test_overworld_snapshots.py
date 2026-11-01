"""Overworld cards show their own view's snapshot (user feedback on v2: cards sometimes
showed another view's image). A swatch test host (standing in for the image view's) renders
one solid colour per view; on the way into the overworld every on-screen view is grabbed
on its own and each card's picture must carry its own colour: also after moves between
visits, when another view covers it (free canvas), and on a scaled display. Offscreen Qt.

Diagnosis (fix-frames): the snapshots were crops of one window grab, so whatever was
painted over a view (another view, a popup, the prompt bar) landed in its card, and on a
scaled display the grab reported device pixel ratio 1 for a 1.5x image, which offset every
crop into a neighbour."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QColor

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item
from specto_app.hosts.base import QuickViewHost, SimpleFactory

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
COLOURS = {"kobi": "#ff0000", "tamu": "#00ff00", "rena": "#0000ff", "pola": "#ffff00"}
SWATCH_QML = "swatch_view.qml"  # written into the test project's folder


class SwatchHost(QuickViewHost):
    def __init__(self, ctx, view_id, manifest):
        super().__init__(ctx, view_id, manifest)
        qml = Path(ctx.engine.project.root) / SWATCH_QML
        self.item = ctx.create_item(qml, properties={"color": COLOURS[view_id]})


def compose(app, spin, positions: dict[str, list]) -> None:
    """Image views whose host is the swatch (view types are a closed set)."""
    (Path(app.project.root) / SWATCH_QML).write_text("import QtQuick\nRectangle { }\n")
    app.hosts.add_factory(SimpleFactory("image", SwatchHost))
    doc = app.live.document()
    for vid, pos in positions.items():
        doc["views"][vid] = {
            "type": "image",
            "label": vid,
            "layout": {"position": pos, "size": [500, 360]},
        }
        pin(doc, vid)
    app.engine.call("canvas.submit", {"document": doc, "label": "swatches"}, AGENT)
    spin(until=lambda: all(v in app.hosts.hosts for v in positions))
    spin(60)


def colour_at(image, window, item) -> str:
    """The grab is in device pixels (and does not say so): scale the item's centre."""
    p = item.mapToScene(item.boundingRect().center())
    k = image.width() / window.width()
    return QColor(image.pixel(int(p.x() * k), int(p.y() * k))).name()


def card_colours(app, spin) -> dict[str, str]:
    """What each overworld card's picture shows, from a grab of the window."""
    root = app.window.contentItem()
    pics = {vid: find_item(root, f"overSnapshot:{vid}") for vid in COLOURS}
    # the picture is visible once its image is ready
    spin(until=lambda: all(p is not None and p.isVisible() for p in pics.values()))
    spin(60)
    image = app.window.grabWindow()
    return {vid: colour_at(image, app.window, p) for vid, p in pics.items()}


def test_every_card_shows_its_own_views_snapshot_also_after_moves_between_visits(app, spin):
    compose(app, spin, {"kobi": [300, 0], "tamu": [820, 0], "rena": [300, 380], "pola": [820, 380]})
    ow = app.overworld
    ow.toggle()
    spin(until=lambda: ow.active)
    # the store holds each view's own colour
    for vid, colour in COLOURS.items():
        img = app.snapshots.get(vid)
        assert img is not None, vid
        assert QColor(img.pixel(img.width() // 2, img.height() // 2)).name() == colour, vid
    assert card_colours(app, spin) == COLOURS

    # move a card (the view list rebuilds), leave, come back: still each its own
    assert ow.endMove("tamu", 840.0, 40.0)
    ow.toggle()
    spin(until=lambda: not ow.active)
    ow.toggle()
    spin(until=lambda: ow.active)
    assert card_colours(app, spin) == COLOURS
    ow.toggle()


def test_a_view_covered_by_another_keeps_its_own_snapshot(app, spin):
    compose(app, spin, {"kobi": [300, 0], "tamu": [820, 0], "rena": [300, 380], "pola": [820, 380]})
    # tamu drops over v2 (the free canvas allows it) and, focused, is drawn on top
    assert app.controller.endMove("tamu", 400.0, 100.0)
    app.controller.focusView("tamu")
    spin(60)
    ow = app.overworld
    ow.toggle()
    spin(until=lambda: ow.active)
    img = app.snapshots.get("kobi")
    assert QColor(img.pixel(img.width() // 2, img.height() // 2)).name() == COLOURS["kobi"]
    # the cards overlap like the views do: tamu moves aside so every picture can be sampled
    assert ow.endMove("tamu", 820.0, 0.0)
    assert card_colours(app, spin) == COLOURS
    ow.toggle()


def test_on_a_scaled_display_every_card_still_shows_its_own_view():
    """The same loop in a process whose Qt scale factor is 1.5 (it must be set before
    the application exists, hence the subprocess)."""
    env = {**os.environ, "QT_SCALE_FACTOR": "1.5"}
    name = test_every_card_shows_its_own_views_snapshot_also_after_moves_between_visits.__name__
    test = f"{Path(__file__).name}::{name}"
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test],
        cwd=Path(__file__).parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]
