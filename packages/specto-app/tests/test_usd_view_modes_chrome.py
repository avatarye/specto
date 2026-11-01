"""The usd view's view-mode pill in the real shell (#153), offscreen with the stub USD
source: a footer pill with the mode's name, always shown (#158), in the accent when the
mode is not lit with what it shows beside it; its menu lists the modes the stage offers
(the one shown bold, collision only with colliders); an undo takes the mode back."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from specto_core import Project

pytest.importorskip("PySide6.QtQuick")

from test_usd_chrome import AGENT, STAGE, add_usd, click, item

from specto_app.app import find_item
from specto_app.views import gallery_native

pytestmark = pytest.mark.skipif(not gallery_native.is_built(), reason=gallery_native.BUILD_HINT)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "demo")
    (proj.root / "worlds").mkdir()
    (proj.root / "worlds" / "valley.usda").write_text(STAGE, encoding="utf-8")
    return proj


@pytest.fixture
def usd(app, spin):
    add_usd(app, spin)
    host = app.hosts.hosts["kobi"]
    spin(until=lambda: app.hosts.ctx.health_of("kobi")["status"] == "ok", timeout=15)
    spin(until=lambda: bool(host.view_modes), timeout=10)
    return host


def test_the_pill_shows_the_mode_at_rest_in_the_footer(app, spin, usd):
    chip = item(app, "viewMode:kobi")
    assert chip.isVisible() and chip.property("text") == "Lit"  # always shown (#158)
    footer = item(app, "footer:kobi")
    assert footer.isAncestorOf(chip)
    assert not chip.property("on")


def test_the_menu_picks_a_mode_and_the_pill_shows_it(app, spin, usd):
    click(app, spin, "viewMode:kobi")
    rows = ["menu:mode-lit", "menu:mode-unlit", "menu:mode-wireframe", "menu:mode-collision"]
    spin(200)  # the menu opens
    for name in rows:
        spin(until=lambda n=name: find_item(app.window.contentItem(), n) is not None)
    assert item(app, "menuText:mode-lit").property("font").bold()
    click(app, spin, "menu:mode-collision")
    spin(until=lambda: usd.view_mode_shown == "collision", timeout=10)
    assert app.live.data["views"]["kobi"]["params"]["view_mode"] == "collision"
    chip = item(app, "viewMode:kobi")
    assert chip.property("text") == "Collision" and chip.property("on")
    note = item(app, "viewModeNote:kobi")
    assert note.isVisible() and note.property("text") == "3 colliders"

    app.engine.call("canvas.undo", {}, AGENT)
    spin(until=lambda: usd.view_mode_shown == "lit", timeout=10)
    assert chip.property("text") == "Lit"
    assert not item(app, "viewModeNote:kobi").isVisible()


def test_collision_is_off_the_menu_without_colliders(app, spin, usd):
    usd.view_modes = [
        {"mode": m, "available": m != "collision", "reason": ""}
        for m in ("lit", "unlit", "wireframe", "collision")
    ]
    usd.bridge.viewModeChanged.emit()
    click(app, spin, "viewMode:kobi")
    spin(until=lambda: find_item(app.window.contentItem(), "menu:mode-wireframe") is not None)
    assert find_item(app.window.contentItem(), "menu:mode-collision") is None
    QTest.keyClick(app.window, Qt.Key.Key_Escape)
