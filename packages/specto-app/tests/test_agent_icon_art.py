"""The agent icon's art (#170) drawn offscreen: the S character on its tile, its eyes in
each icon state, the gaze, and motion that stops with the window. The icon is grabbed at
twice its size (104 px) so samples land well inside the stroke and the eyes. Every
expected place comes from the art (``specto_app.logo``), so a swap of the design files
keeps these tests meaningful."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMetaObject, QObject, QUrl
from PySide6.QtGui import QColor, QVector2D

from specto_app.logo import gaze_offset, inside_points, load_logo, logo
from specto_app.prompt.icon_pose import art_for_qml, pose
from specto_app.qt_setup import install_qml

SIZE = 104
AT = 20  # the icon's top-left in the window
ART = logo()

QML = f"""
import QtQuick
import Specto.Shell
Rectangle {{
    width: 144; height: 144
    color: "#204060"
    AgentIcon {{ objectName: "art"; x: {AT}; y: {AT}; width: {SIZE}; height: {SIZE} }}
}}
"""


def open_icon(spin, tmp_path: Path, art=None):
    """The icon in its own offscreen window; ``art`` replaces the shipped design files."""
    from PySide6.QtQuick import QQuickView

    from specto_app.app import find_item

    view = QQuickView()
    theme = install_qml(view.engine())
    if art is not None:
        view.engine().rootContext().setContextProperty("logoArt", art_for_qml(art))
    src = tmp_path / "icon.qml"
    src.write_text(QML, encoding="utf-8")
    view.setSource(QUrl.fromLocalFile(str(src)))
    assert view.rootObject() is not None, view.errors()
    view.show()
    item = find_item(view.rootObject(), "art")
    spin(60)
    return view, item, theme


@pytest.fixture
def icon(qapp, spin, tmp_path: Path):
    view, art, theme = open_icon(spin, tmp_path)
    yield view, art, theme.tokens()["colors"]
    view.close()
    view.deleteLater()
    spin(20)


def px(gx: float, gy: float, art=ART) -> tuple[int, int]:
    """A point of the mark's 100-unit grid in window pixels."""
    grid = art.body
    k = SIZE / grid.canvas
    return (
        round(AT + (grid.origin + gx * grid.scale) * k),
        round(AT + (grid.origin + gy * grid.scale) * k),
    )


def colour_at(view, gx: float, gy: float, art=ART) -> QColor:
    image = view.grabWindow()
    k = image.width() / view.width()
    x, y = px(gx, gy, art)
    return QColor(image.pixel(int(x * k), int(y * k)))


def close_to(c: QColor, hex_: str, tol: int = 24) -> bool:
    want = QColor(hex_)
    return all(abs(a - b) <= tol for a, b in zip(c.getRgb()[:3], want.getRgb()[:3], strict=True))


def blend(fg: str, bg: str, alpha: float) -> str:
    f, b = QColor(fg), QColor(bg)
    mix = [
        round(alpha * x + (1 - alpha) * y)
        for x, y in zip(f.getRgb()[:3], b.getRgb()[:3], strict=True)
    ]
    return QColor(*mix).name()


def set_state(art, spin, state: str, ms: int = 350) -> None:
    art.setProperty("state", state)
    spin(ms)


# points the S covers (stroked or filled), well in from its edge
BODY_POINTS = inside_points(ART.body.body, depth=3, step=4)[::7]
# the tile, left of the mark
TILE_POINT = (-ART.body.origin / ART.body.scale / 2, 50)


def eye_centres(art) -> list[tuple[float, float]]:
    dx, dy = art.property("eyeDx"), art.property("eyeDy")
    eyes = ART.wide if art.property("eyeStyle") == "wide" else ART.open
    return [(e.cx + dx, e.cy + dy) for e in eyes]


@pytest.mark.parametrize("state", ["idle", "thinking", "working", "needs_you"])
def test_each_state_paints_the_s_with_both_eyes(icon, spin, state):
    view, art, colors = icon
    set_state(art, spin, state)
    want = pose(state)
    assert art.property("eyeStyle") == want.eyes
    assert art.property("motion") == want.motion
    assert art.property("bodyOpacity") == pytest.approx(want.body_opacity)
    for gx, gy in BODY_POINTS:
        c = colour_at(view, gx, gy)
        assert close_to(c, colors[want.body]), (state, (gx, gy), c.name())
    assert close_to(colour_at(view, *TILE_POINT), colors[want.tile], tol=6)
    # moving eyes: sample them still (the art's own offsets say where they are now)
    for loop in ("lookAnim", "scanAnim"):
        anim = art.findChild(QObject, loop)
        if anim is not None and anim.property("running"):
            anim.setProperty("paused", True)
    for cx, cy in eye_centres(art):
        c = colour_at(view, cx, cy)
        assert close_to(c, colors["agent"]), (state, (cx, cy), c.name())


def test_error_shows_x_eyes_over_the_s_at_45_percent(icon, spin):
    view, art, colors = icon
    set_state(art, spin, "error")
    assert art.property("eyeStyle") == "x" and art.property("motion") == "none"
    from specto_app.app import find_item

    assert find_item(art, "agentIconCross").isVisible()
    assert not find_item(art, "agentIconEyes").isVisible()
    faded = blend(colors["text"], colors["surface"], 0.45)
    for gx, gy in BODY_POINTS:
        assert close_to(colour_at(view, gx, gy), faded, tol=14), (gx, gy)
    # each X crosses at its centre, in the removed colour, and leaves its open top side
    # clear (where an eye dot would be painted)
    for cross in ART.x:
        x0, y0, x1, y1 = cross.bounds()
        c = colour_at(view, (x0 + x1) / 2, (y0 + y1) / 2)
        assert close_to(c, colors["removed"]), c.name()
        assert close_to(colour_at(view, (x0 + x1) / 2, y0 - 2), colors["surface"], tol=6)


@pytest.mark.parametrize("gaze", [(1, 0), (-1, 0), (0, -1), (0.5, 0.5), (3, 4)])
def test_the_eyes_follow_the_gaze_within_their_travel(icon, spin, gaze):
    view, art, colors = icon
    offset = gaze_offset(*gaze)  # the Python seam; the QML computes its own
    art.setProperty("gaze", QVector2D(*gaze))
    spin(450)  # eased over 250 ms
    assert art.property("eyeDx") == pytest.approx(offset[0], abs=0.05)
    assert art.property("eyeDy") == pytest.approx(offset[1], abs=0.05)
    for cx, cy in eye_centres(art):
        assert close_to(colour_at(view, cx, cy), colors["agent"])


def test_working_scans_left_and_right_inside_the_bowl(icon, spin):
    _, art, _ = icon
    art.setProperty("state", "working")
    seen = []
    for _ in range(30):
        spin(80)
        seen.append(art.property("eyeDx"))
        assert art.property("eyeDy") == 0
    tx = ART.travel[0]
    assert min(seen) >= -tx and max(seen) <= tx
    assert max(seen) - min(seen) > tx  # it moves across, not only at one end


def test_thinking_looks_around_up_left_and_up_right(icon, spin):
    _, art, _ = icon
    art.setProperty("state", "thinking")
    seen = []
    for _ in range(45):
        spin(100)
        seen.append((art.property("eyeDx"), art.property("eyeDy")))
    at_x, at_y = pose("thinking").offset
    assert all(-at_x <= dx <= at_x and dy == at_y for dx, dy in seen)
    assert min(dx for dx, _ in seen) == pytest.approx(-at_x, abs=0.3)
    assert max(dx for dx, _ in seen) == pytest.approx(at_x, abs=0.3)


def test_motion_stops_while_the_window_is_hidden(icon, spin):
    view, art, _ = icon
    set_state(art, spin, "working", 100)
    assert art.property("live") and art.property("motionRunning")
    view.hide()
    spin(60)
    assert not art.property("live") and not art.property("motionRunning")
    view.show()
    spin(60)
    assert art.property("motionRunning")


def test_idle_blinks_by_closing_the_eyes(icon, spin):
    _, art, _ = icon
    blinks = art.findChild(QObject, "blinkTimer")
    assert blinks is not None and blinks.property("running")
    assert 4000 <= blinks.property("interval") <= 7000
    QMetaObject.invokeMethod(art, "blink")
    spin(50)
    assert art.property("lid") < 0.9  # closing, toward the blink's key pose
    spin(60)
    assert art.property("lid") >= ART.blink_lid - 0.01
    spin(300)
    assert art.property("lid") == pytest.approx(1)


def test_a_filled_outline_body_is_painted_as_a_fill(qapp, spin, tmp_path):
    """Design files whose body is a filled outline (here a square ring): the icon paints
    the ring in ink and leaves its hole the tile's colour."""
    from test_logo import write_set

    (tmp_path / "art").mkdir()
    art = load_logo(write_set(tmp_path / "art", filled=True))
    view, _, theme = open_icon(spin, tmp_path, art)
    try:
        colors = theme.tokens()["colors"]
        assert close_to(colour_at(view, 25, 50, art), colors["text"])
        assert close_to(colour_at(view, 75, 60, art), colors["text"])
        assert close_to(colour_at(view, 50, 62, art), colors["surface"], tol=6)  # the hole
        cx, cy = art.open[0].cx, art.open[0].cy
        assert close_to(colour_at(view, cx, cy, art), colors["agent"])
    finally:
        view.close()
        view.deleteLater()
        spin(20)


def test_wide_eyes_follow_the_gaze_within_their_own_travel(icon, spin):
    view, art, colors = icon
    set_state(art, spin, "needs_you")
    art.setProperty("gaze", QVector2D(1, 0))
    spin(450)
    want = gaze_offset(1, 0, ART.wide_travel)
    assert art.property("eyeDx") == pytest.approx(want[0], abs=0.05)
    assert art.property("eyeDy") == pytest.approx(want[1], abs=0.05)
    for cx, cy in eye_centres(art):
        assert close_to(colour_at(view, cx, cy), colors["agent"])
