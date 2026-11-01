"""Bench the canvas tile floor (#191, #201): ``TileFloor.qml`` with the real ``TileFloor`` on its
real clock, rendered offscreen with OpenGL (no window on screen), idle, while the agent
thinks, and under a pan.

    uv run python tools/bench_floor.py [--width 1280] [--height 742] [--seconds 3]

Reports, per phase:

- requests: frames Qt Quick asked for (``renderRequested`` + ``sceneChanged``), per s;
- layer renders: frames whose floor uniforms changed (the floor's pass ran), per s;
- tick us: the Python cue tick (``TileFloor.tick``), mean / p95, GUI thread;
- cpu %: the process's CPU time over the phase's wall time (one core = 100 %);
- frame ms: one Qt Quick frame (polish, sync, render, glFinish), wall time, mean / p95;
- gpu ms: the same frame's GPU time (an OpenGL timer query), mean / p95; against the
  same scene with the floor hidden.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "specto-app" / "src"))

from specto_app.frames.offscreen import OffscreenQuick, prepare_environment

prepare_environment()

from PySide6.QtCore import Property, QObject, Signal  # noqa: E402
from PySide6.QtGui import QGuiApplication, QOpenGLContext  # noqa: E402
from PySide6.QtOpenGL import QOpenGLTimerQuery  # noqa: E402

from specto_app.canvas.tile_floor import TileFloor  # noqa: E402
from specto_app.qt_setup import install_qml  # noqa: E402

QML = """import QtQuick
import Specto.Shell
Item {
    width: %(w)d; height: %(h)d
    Rectangle { anchors.fill: parent; color: theme.colors.bg }
    TileFloor { objectName: "floor"; anchors.fill: parent }
}
"""


class Controller(QObject):
    viewIdsChanged = Signal()
    selectionChanged = Signal()
    dragRectsChanged = Signal()
    wiringChanged = Signal()

    def __init__(self, w: float, h: float) -> None:
        super().__init__()
        self.size = (w, h)

    @Property("QVariantList", notify=viewIdsChanged)
    def viewIds(self):
        return []

    @Property("QVariantList", notify=selectionChanged)
    def selectedViews(self):
        return []

    @Property("QVariantMap", notify=dragRectsChanged)
    def dragRects(self):
        return {}

    @Property(bool, notify=wiringChanged)
    def wiring(self):
        return False

    def viewport_rect(self):
        return (0.0, 0.0, *self.size)

    def chain_of(self, vid: str) -> str:
        return "chain_0"

    def jumpTo(self, vid: str) -> bool:
        return True


class Icon(QObject):
    changed = Signal()
    state = "idle"


class Bar(QObject):
    turnFinished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.icon = Icon()


def stats(xs: list[float]) -> str:
    if not xs:
        return "-"
    xs = sorted(xs)
    return f"{statistics.fmean(xs):.3f} / {xs[int(0.95 * (len(xs) - 1))]:.3f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=742)
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument(
        "--res",
        type=int,
        default=0,
        help="screen px per texture px to try (1: full, the default since #201; 2: half); "
        "the cell stays 8 px",
    )
    ns = ap.parse_args()
    if ns.res:
        from specto_app.canvas import floor as floor_maths

        floor_maths.RES, floor_maths.CELL_TEXELS = ns.res, 8 // ns.res
    app = QGuiApplication([])
    quick = OffscreenQuick(ns.width, ns.height)
    theme = install_qml(quick.engine)
    bar = Bar()
    floor = TileFloor(Controller(ns.width, ns.height), prompt_bar=bar, theme=theme)
    quick.engine.rootContext().setContextProperty("tileFloor", floor)
    root = quick.load(QML % {"w": ns.width, "h": ns.height})
    item = next(c for c in root.childItems() if c.objectName() == "floor")
    floor.setAgentAnchor(38.0, ns.height - 38.0)
    gl = QOpenGLContext.currentContext().functions()
    count = {"req": 0, "layer": 0, "pending": False}

    def requested() -> None:
        count["req"] += 1
        count["pending"] = True

    quick.control.renderRequested.connect(requested)
    quick.control.sceneChanged.connect(requested)
    # the layer's pass runs when a uniform of its ShaderEffect changes: the cues', the
    # grid's (a resize), or the camera's (a pan, only while a cue sits on a view)
    fx = next(c for c in item.childItems() if c.objectName() == "tileFloorEffect")

    def layer() -> None:
        count["layer"] += 1

    floor.uniformsChanged.connect(layer)
    for sig in ("camChanged", "cellsChanged", "texSizeChanged"):
        getattr(fx, sig).connect(layer)
    ticks: list[float] = []
    real_tick = floor.tick

    def timed_tick() -> None:
        t0 = time.perf_counter()
        real_tick()
        ticks.append((time.perf_counter() - t0) * 1e6)

    floor._timer.timeout.disconnect(floor.tick)
    floor._timer.timeout.connect(timed_tick)

    query = QOpenGLTimerQuery()
    query.create()
    gpu: list[float] = []

    def render() -> float:
        t0 = time.perf_counter()
        query.begin()
        quick.render()
        query.end()
        gl.glFinish()
        gpu.append(query.waitForResult() / 1e6)
        return (time.perf_counter() - t0) * 1000

    def phase(name: str, step=None) -> None:
        for _ in range(5):
            app.processEvents()
            render()
        count["req"] = count["layer"] = 0
        count["pending"] = False
        ticks.clear()
        gpu.clear()
        frames: list[float] = []
        cpu0, wall0 = time.process_time(), time.perf_counter()
        end = wall0 + ns.seconds
        i = 0
        while time.perf_counter() < end:
            app.processEvents()
            if step is not None:
                step(i)
            if count["pending"] or step is not None:
                count["pending"] = False
                frames.append(render())
            i += 1
            time.sleep(1 / 60)
        wall = time.perf_counter() - wall0
        cpu = time.process_time() - cpu0
        print(
            f"[OK] {name}: requests {count['req'] / wall:.1f}/s, layer renders "
            f"{count['layer'] / wall:.1f}/s, tick us {stats(ticks)}, cpu {100 * cpu / wall:.1f} %, "
            f"frame ms {stats(frames)}, gpu ms {stats(gpu)} ({len(frames)} frames)"
        )

    print(
        f"[OK] floor {ns.width} x {ns.height}, texture {item.property('grid')['texW']} x "
        f"{item.property('grid')['texH']}"
    )
    phase("idle")
    bar.icon.state = "thinking"
    bar.icon.changed.emit()
    phase("thinking")
    bar.icon.state = "idle"
    bar.icon.changed.emit()
    for _ in range(120):  # the last rings go out
        app.processEvents()
        time.sleep(1 / 60)
    x0 = item.property("originX")
    phase("pan 1 px/frame", lambda i: item.setProperty("originX", x0 + i))
    item.setProperty("originX", x0)
    item.setProperty("visible", False)
    phase("floor hidden, pan frames (baseline)", lambda i: None)
    item.setProperty("visible", True)
    phase("floor, unchanged frames", lambda i: None)
    floor.close()
    query.destroy()
    quick.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
