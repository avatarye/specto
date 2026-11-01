"""Child process of test_tile_floor_render.py: ``TileFloor.qml`` with the real
``TileFloor`` (fed by a stand-in canvas controller and agent icon) rendered with OpenGL
into an offscreen FBO, no window on screen. Runs the scenarios below on a fake clock and
prints one ``RESULT {json}`` line.

    python tile_floor_child.py
"""

from __future__ import annotations

import json
import sys

from specto_app.frames.offscreen import OffscreenQuick, prepare_environment

prepare_environment()

from PySide6.QtGui import QGuiApplication  # noqa: E402
from tile_floor_helpers import Bar, Clock, Controller  # noqa: E402

from specto_app.canvas.tile_floor import TileFloor  # noqa: E402
from specto_app.qt_setup import install_qml  # noqa: E402

W, H = 320, 240
BG = [244, 239, 230]  # the theme's bg

QML = """import QtQuick
import Specto.Shell
Item {
    width: %(w)d; height: %(h)d
    Rectangle { anchors.fill: parent; color: theme.colors.bg }
    TileFloor { id: floor; anchors.fill: parent }
}
"""


def main() -> int:
    app = QGuiApplication([])
    quick = OffscreenQuick(W, H)
    theme = install_qml(quick.engine)
    controller, bar, clock = Controller(W, H), Bar(), Clock()
    floor = TileFloor(controller, prompt_bar=bar, theme=theme, clock=clock)
    floor._timer.stop()  # the scenarios step the fake clock themselves
    quick.engine.rootContext().setContextProperty("tileFloor", floor)
    root = quick.load(QML % {"w": W, "h": H})
    item = next(c for c in _all(root) if c.objectName() == "tileFloor")
    fx = next(c for c in _all(item) if c.objectName() == "tileFloorEffect")
    requests = {"n": 0}
    quick.control.renderRequested.connect(lambda: requests.__setitem__("n", requests["n"] + 1))
    quick.control.sceneChanged.connect(lambda: requests.__setitem__("n", requests["n"] + 1))
    uniform_changes = {"n": 0}
    for sig in ("camChanged", "cellsChanged", "texSizeChanged", "g0Changed", "p0Changed"):
        getattr(fx, sig).connect(lambda: uniform_changes.__setitem__("n", uniform_changes["n"] + 1))

    def frame(n: int = 2):
        for _ in range(n):
            app.processEvents()
            quick.render()
        return quick.grab()

    def px(img, x, y):
        return list(img.pixelColor(x, y).getRgb()[:3])

    def at(t: float):
        clock.t = t
        floor.tick()
        floor._timer.stop()
        return frame()

    out: dict = {"gpu": item.property("gpu")}

    # rest: the screen-fixed grid of a 320 x 240 area: 8 px cells from the top-left (no
    # margin: 40 x 30 whole cells), 3 px tiles [2, 5) + 8 k, gaps around them
    item.setProperty("originX", 12.0)
    item.setProperty("originY", 12.0)
    img = frame(3)
    out["rest"] = {
        "tile": px(img, 27, 27),
        "tile_edge": px(img, 28, 28),
        "corner": px(img, 26, 26),
        "gap": px(img, 30, 27),
        "gap_before": px(img, 25, 27),
        "gap_corner": px(img, 31, 31),
    }

    # idle: nothing renders, nothing asks to
    requests["n"] = 0
    for _ in range(50):
        app.processEvents()
    out["idle_requests"] = requests["n"]
    out["idle_ticking"] = floor.ticking

    # a pan or a zoom with no cue on a view: no uniform changes, the floor stays still to
    # the bit (a slow pan of 0.25 px a frame over two cells, then a zoom)
    uniform_changes["n"] = 0
    first = frame(2)
    worst = 0
    for f in range(1, 65):
        item.setProperty("originX", 12.0 + 0.25 * f)
        if f > 48:
            item.setProperty("zoom", 1.0 - 0.02 * (f - 48))
        img = frame(1)
        diff = 0
        for y in range(0, H, 3):
            for x in range(0, W, 3):
                a, b = img.pixelColor(x, y), first.pixelColor(x, y)
                diff = max(diff, abs(a.red() - b.red()) + abs(a.green() - b.green()))
        worst = max(worst, diff)
    out["pan_uniform_changes"] = uniform_changes["n"]
    out["pan_max_diff"] = worst
    item.setProperty("originX", 12.0)
    item.setProperty("zoom", 1.0)
    frame(2)

    # a lit band: thinking at the S icon (screen space), its first band 0.2 s out:
    # R = 24 + 560 easeOut(0.2 / 1.8) = 141.5 px from (30, 210), A 0.99, w 32
    floor.setAgentAnchor(30.0, 210.0)
    bar.icon.state = "thinking"
    clock.t = 200.0
    bar.icon.changed.emit()
    img = at(200.2)
    # the cell whose centre (172, 212) is 142 px right of the icon: [168, 176) x [208, 216);
    # at its crest the tile is 6 px at offset 1, [169, 175), with a 2 px gap [175, 177)
    out["band"] = {
        "tile": px(img, 171, 211),
        "grown_left": px(img, 169, 211),
        "grown_right": px(img, 174, 211),
        "gap": px(img, 175, 211),
        "gap_right": px(img, 176, 211),
        "gap_left": px(img, 168, 211),
        "rest_far": px(img, 27, 27),
    }
    out["band_ticking"] = floor.cues.animating(200.2)
    bar.icon.state = "idle"
    bar.icon.changed.emit()
    at(210.0)

    # the selection footprint: a view at world (96, 96, 64, 48) (screen 108.., the origin 12)
    controller._ids = [{"id": "kobi", "x": 96.0, "y": 96.0, "w": 64.0, "h": 48.0}]
    controller.viewIdsChanged.emit()  # a new view on screen: bands; let them finish
    at(213.0)
    controller._sel = ["kobi"]
    controller.selectionChanged.emit()
    img = at(213.5)
    # the view is screen [108, 172) x [108, 156); the cell left of it, [96, 104), has its
    # centre 8 px out (e 0.24 at w 20, A 0.4: a 4 px tile [98, 102)); under the view d = 0
    # (e 0.4: 4 px, [122, 126), #220)
    out["tie"] = {
        "near": px(img, 98, 130),
        "far": px(img, 27, 27),
        "inside_tile": px(img, 122, 130),
        "inside_gap": px(img, 127, 130),
        # the falloff: the tiles' widths along y = 131 over the five cells left of the
        # view's edge cell (centres 40, 32, 24, 16 and 8 px out; w 20 at A 0.4 grows the
        # last one, the one before it only tints)
        "widths_to_edge": _runs([px(img, x, 131) != BG for x in range(64, 104)]),
    }
    # the footprint follows its view across a pan (the camera goes in while it shows)
    uniform_changes["n"] = 0
    item.setProperty("originX", 12.0 + 40)
    img = at(213.6)
    out["tie_panned"] = {"near": px(img, 138, 130), "left_behind": px(img, 98, 130)}
    out["tie_pan_uniform_changes"] = uniform_changes["n"]
    item.setProperty("originX", 12.0)
    at(213.7)
    out["tie_ticking_after_fade"] = floor.cues.animating(213.5)
    requests["n"] = 0
    for _ in range(50):
        app.processEvents()
    out["tie_idle_requests"] = requests["n"]

    # the wiring layer dims the tiles to 30 %
    controller._sel = []
    controller.selectionChanged.emit()
    at(220.0)
    controller._wiring = True
    controller.wiringChanged.emit()
    img = frame(2)
    out["dim"] = {"tile": px(img, 27, 27), "dim": floor.property("dim")}
    quick.close()
    print("RESULT " + json.dumps(out))
    return 0


def _runs(row: list[bool]) -> list[int]:
    """The lengths of the runs of True in ``row``."""
    out, n = [], 0
    for v in [*row, False]:
        if v:
            n += 1
        elif n:
            out.append(n)
            n = 0
    return out


def _all(item):
    stack = [item]
    while stack:
        it = stack.pop()
        yield it
        stack.extend(it.childItems())


if __name__ == "__main__":
    sys.exit(main())
