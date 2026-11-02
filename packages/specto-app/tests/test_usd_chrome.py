"""The usd view's chrome in the real shell (#139), offscreen with the stub USD source:
the VIEW panel (camera speed, World origin, the renderer readout) on the left, the PRIMS
outliner on the right, both the side panel standard with their state in
``params.panels``, and the shared footer (previous / next stage, frame all, the stage's
name, the time and cost readouts). The FrameItem draws nothing under the software
backend; the seam-3 flow shows the frames."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from specto_core import CallContext, Project

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item
from specto_app.views import gallery_native

pytestmark = pytest.mark.skipif(not gallery_native.is_built(), reason=gallery_native.BUILD_HINT)

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
STAGE = '#usda 1.0\n(\n    upAxis = "Z"\n)\ndef Xform "World" {}\n'


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "demo")
    (proj.root / "worlds").mkdir()
    for name in ("valley", "stage-a"):
        (proj.root / "worlds" / f"{name}.usda").write_text(STAGE, encoding="utf-8")
    return proj


def item(app, name: str):
    found = find_item(app.window.contentItem(), name)
    assert found is not None, name
    return found


def click(app, spin, name: str) -> None:
    it = item(app, name)
    p = it.mapToScene(it.boundingRect().center()).toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    spin(10)


def add_usd(app, spin) -> None:
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "usd",
        "label": "World",
        "layout": {"position": [380, 40], "size": [760, 420]},
    }
    pin(doc, "kobi", sources=["worlds"])
    app.engine.call("canvas.submit", {"document": doc, "label": "add kobi"}, AGENT)
    spin(until=lambda: "kobi" in app.hosts.hosts)


@pytest.fixture
def usd(app, spin):
    add_usd(app, spin)
    spin(until=lambda: app.hosts.ctx.health_of("kobi")["status"] == "ok", timeout=15)
    return app.hosts.hosts["kobi"]


def test_the_picture_says_the_start_phase_until_the_stage_is_up(
    app, spin, monkeypatch: pytest.MonkeyPatch
):
    """The loading label (#156): the stage's name and the phase over the picture, until
    the first frame (the software backend draws no frames, so the label lifts then)."""
    monkeypatch.setenv("SPECTO_USD_STUB_PHASE_MS", "400")
    add_usd(app, spin)
    cover, phase = item(app, "cover:kobi"), item(app, "coverPhase:kobi")
    spin(until=lambda: phase.property("text") == "opening stage", timeout=15)
    assert cover.isVisible() and item(app, "coverFile:kobi").property("text") == "stage-a.usda"
    assert not item(app, "phase:kobi").isVisible()
    spin(until=lambda: phase.property("text") == "rendering the first frame", timeout=15)
    spin(until=lambda: app.hosts.ctx.health_of("kobi")["status"] == "ok", timeout=15)
    spin(until=lambda: not cover.isVisible(), timeout=5)


def spy(camera, name: str) -> list[str]:
    """Record calls of the camera seam's ``name`` (the camera itself is #138's Nav)."""
    calls: list[str] = []
    real = getattr(camera, name)

    def record(*args):
        calls.append(name)
        return real(*args)

    setattr(camera, name, record)
    return calls


def test_the_footer_steps_through_the_stages_and_names_the_one_shown(app, spin, usd):
    assert item(app, "footer:kobi").height() == 24
    assert item(app, "position:kobi").property("text") == "1 / 2"
    assert item(app, "step:kobi:prev").property("tip") == "previous (PageUp)"
    assert item(app, "stage:kobi").property("text") == "stage-a.usda"
    assert item(app, "frameAll:kobi").property("name") == "target"
    # a static stage (#141): no scrubber, no play, no time readout
    assert not item(app, "time:kobi").isVisible() and not item(app, "play:kobi").isVisible()
    assert not item(app, "usd:kobi:track").isVisible()
    assert item(app, "cost:kobi").property("text").endswith("ms \u00b7 \u2013 GB")

    click(app, spin, "step:kobi:next")
    assert item(app, "stage:kobi").property("text") == "valley.usda"
    assert item(app, "step:kobi:next").property("disabled")
    calls = spy(usd.camera, "frame_all")
    click(app, spin, "frameAll:kobi")
    assert calls == ["frame_all"]


def test_the_view_panel_shows_speed_world_origin_and_the_renderer(app, spin, usd):
    panel = item(app, "panel:kobi:view")
    assert panel.property("title") == "View" and panel.width() == 200
    spin(until=lambda: "Stub" in str(item(app, "renderer:kobi").childItems()[1].property("text")))
    calls = spy(usd.camera, "world_origin")
    click(app, spin, "origin:kobi")
    assert calls == ["world_origin"]

    slider = item(app, "speed:kobi")
    right = slider.mapToScene(QPointF(slider.width() - 1, slider.height() / 2)).toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, right)
    spin(until=lambda: app.live.data["views"]["kobi"]["params"].get("camera_speed") == 8.0)
    assert usd.camera.camera_speed == 8.0


def test_the_prims_outliner_lists_the_stage_and_opens_rows(app, spin, usd):
    spin(until=lambda: find_item(app.window.contentItem(), "prim:kobi:/World/Vehicles"))
    click(app, spin, "toggle:kobi:/World/Vehicles")
    spin(until=lambda: find_item(app.window.contentItem(), "prim:kobi:/World/Vehicles/Van_000"))
    rows = usd.outliner_rows()
    assert rows[5]["name"] == "Van_000" and rows[-1] == {
        "kind": "more", "path": "/World/Vehicles", "depth": 2, "count": 150,
    }  # fmt: skip


def test_the_panels_collapse_and_come_back_from_the_params(app, spin, usd):
    click(app, spin, "panel:kobi:prims:toggle")
    params = app.live.data["views"]["kobi"]["params"]
    assert params["panels"] == {"prims": {"collapsed": True, "width": 240}}
    assert item(app, "panel:kobi:prims").width() == 24
    click(app, spin, "panel:kobi:view:toggle")
    assert item(app, "panel:kobi:view").width() == 24

    app.engine.call("canvas.undo", {}, AGENT)
    spin(until=lambda: item(app, "panel:kobi:view").width() == 200)
    click(app, spin, "panel:kobi:prims:expand")
    assert item(app, "panel:kobi:prims").width() == 240


def test_clicks_select_prims_in_the_picture_and_the_outliner(app, spin, usd):
    # #140: the stub's picture has the second block's base low in the middle
    viewport = item(app, "viewport:kobi")
    at = viewport.mapToScene(QPointF(viewport.width() * 0.5, viewport.height() * 0.8)).toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    base = "/World/Blocks/Block_02/Base"
    spin(until=lambda: usd.selected == [base], timeout=10)
    # revealed: the outliner opened down to the row and marks it
    spin(until=lambda: find_item(app.window.contentItem(), f"prim:kobi:{base}"), timeout=10)
    spin(until=lambda: item(app, f"prim:kobi:{base}").property("chosen"))

    click(app, spin, "prim:kobi:/World/Sky")  # a row click selects that prim instead
    assert usd.selected == ["/World/Sky"]
    assert not item(app, f"prim:kobi:{base}").property("chosen")

    calls = spy(usd.camera, "frame")
    row = item(app, "prim:kobi:/World/Blocks/Block_01")
    p = row.mapToScene(row.boundingRect().center()).toPoint()
    QTest.mouseDClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    spin(until=lambda: calls == ["frame"], timeout=10)
    assert usd.selected == ["/World/Blocks/Block_01"]


ANIMATED = """#usda 1.0
(
    upAxis = "Z"
    startTimeCode = 0
    endTimeCode = 48
    timeCodesPerSecond = 24
)
def Xform "World"
{
    double3 xformOp:translate.timeSamples = { 0: (0, 0, 0), 48: (4, 0, 0) }
    uniform token[] xformOpOrder = ["xformOp:translate"]
}
"""


def test_an_animated_stage_shows_the_scrubber_play_and_its_time(app, spin, project):
    """#141: the video's scrubber above the footer, play / pause and the time readout on
    its right; a press on the scrubber moves the stage."""
    (project.root / "worlds" / "a_drive.usda").write_text(ANIMATED, encoding="utf-8")
    add_usd(app, spin)
    spin(until=lambda: app.hosts.ctx.health_of("kobi")["status"] == "ok", timeout=15)
    host = app.hosts.hosts["kobi"]
    assert host.path.endswith("a_drive.usda")
    spin(until=lambda: item(app, "usd:kobi:track").isVisible())
    assert item(app, "time:kobi").property("text") == "0 / 0-48 @ 24 tc/s"
    assert item(app, "play:kobi").property("name") == "play"

    track = item(app, "usd:kobi:track")
    at = track.mapToScene(track.boundingRect().center()).toPoint()  # the middle: 1 s
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    spin(until=lambda: item(app, "time:kobi").property("text").startswith("24 /"))

    click(app, spin, "play:kobi")
    assert host.playing and item(app, "play:kobi").property("name") == "pause"
    spin(until=lambda: host.timecode > 26)
    click(app, spin, "play:kobi")
    assert not host.playing

    click(app, spin, "step:kobi:next")  # on to a static stage: the transport goes
    spin(until=lambda: not item(app, "usd:kobi:track").isVisible())
    assert not item(app, "play:kobi").isVisible()


def source_target(usd) -> list[float] | None:
    camera = usd.snapshot()["source"].get("camera")
    return [round(c, 6) for c in camera["target"]] if camera else None


def test_f_frames_the_selection_else_the_whole_stage(app, spin, usd):
    """#151: F in the focused view frames what is selected, the whole stage when nothing
    is. (Before, F only ever framed the stage: on a world centred on the origin that is
    the first look again, so after an orbit F changed next to nothing.)"""
    viewport = item(app, "viewport:kobi")
    at = viewport.mapToScene(QPointF(viewport.width() * 0.15, viewport.height() * 0.8)).toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    spin(until=lambda: usd.selected == ["/World/Blocks/Block_01/Base"], timeout=10)
    QTest.keyClick(app.window, Qt.Key.Key_F)
    # the stub's first block is 16 x 12 x 12 around x = -40 (Z-up); its base, the lower
    # half (#39: a prim's own subtree), is centred at z 3
    spin(until=lambda: source_target(usd) == [-40.0, 0.0, 3.0], timeout=10)

    usd.select([])
    QTest.keyClick(app.window, Qt.Key.Key_F)
    spin(until=lambda: source_target(usd) == [0.0, 0.0, 6.0], timeout=10)  # the stage's centre


def test_the_viewport_hands_every_button_and_chord_to_the_camera(app, spin, usd):
    """#151: each button goes down and up on its own, so LMB+RMB is a chord; a drag never
    picks, nor does an Alt click; RMB held with W flies until either comes up."""
    viewport = item(app, "viewport:kobi")
    at = viewport.mapToScene(QPointF(viewport.width() * 0.15, viewport.height() * 0.8)).toPoint()
    nav = usd.camera.nav
    left, right = Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton
    none, alt = Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.AltModifier
    win = app.window

    QTest.mousePress(win, left, none, at)
    QTest.mousePress(win, right, none, at)
    spin(5)
    assert nav.gesture == "pan"
    QTest.mouseRelease(win, right, none, at)
    QTest.mouseRelease(win, left, none, at)
    spin(5)
    assert nav.gesture is None

    QTest.mousePress(win, left, alt, at)
    spin(5)
    assert nav.gesture == "orbit"
    QTest.mouseRelease(win, left, alt, at)
    spin(200)
    assert usd.selected == []  # an Alt click orbits, it does not pick

    yaw = nav.yaw
    QTest.mousePress(win, left, none, at)
    QTest.mouseMove(win, at + QPoint(40, 0))
    QTest.mouseRelease(win, left, none, at + QPoint(40, 0))
    spin(200)
    assert nav.yaw > yaw and usd.selected == []  # a drag turns, it does not pick

    before = nav.position
    QTest.mousePress(win, right, none, at)
    QTest.keyPress(win, Qt.Key.Key_W)
    spin(until=lambda: nav.position != before, timeout=2)
    QTest.mouseRelease(win, right, none, at)
    spin(5)
    assert not nav.moving
    QTest.keyRelease(win, Qt.Key.Key_W)
