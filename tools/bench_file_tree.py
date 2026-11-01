"""Benchmark for #115: the file tree scrolling a 20k-file tree, details off and on.

    uv run python tools/bench_file_tree.py [--files 20000] [--seconds 8] [--warmup 2]
        [--speed 3000] [--modes off,on,off,on] [--out out/verify/v2/bench_file_tree.json]

``FileTreeView.qml`` alone in a real (GPU) window, 320 x 900, fed by a ``FileTreeModel``
over ``--files`` synthetic paths (200 folders, all open, so every file is a row) with
sizes and times spread over the last few days (every age colour shows). No disk is
read. The list scrolls at ``--speed`` px/s, bouncing at the ends, driven by a precise
4 ms timer from wall time. Per mode, measured after ``--warmup``:

- window frame rate and frame intervals (``QQuickWindow.frameSwapped``, queued to the
  GUI thread): p50 / p95 / p99 / max, and the share over 20 ms (a missed 60 Hz frame)
- GUI-thread busy: ``time.thread_time()`` over wall time
- rows scrolled per second and the model's ``data()`` calls per second

Writes JSON to ``--out`` and a markdown summary next to it. The details setting is a
stand-in object here: the user's own settings are never touched.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import sys
import time
from pathlib import Path

from PySide6.QtCore import Property, QElapsedTimer, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQuick import QQuickItem, QQuickView

from specto_app.qt_setup import install_qml, prepare
from specto_app.theme import COLORS
from specto_app.views import file_tree as ft

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "out" / "verify" / "v2" / "bench_file_tree.json"  # git-ignored


class Details(QObject):
    """Stands in for ``viewSettings`` (no persistence)."""

    treeDetailsChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._on = False

    @Property(bool, notify=treeDetailsChanged)
    def treeDetails(self) -> bool:
        return self._on

    def set(self, on: bool) -> None:
        self._on = on
        self.treeDetailsChanged.emit()


class Bridge(QObject):
    """The view's gestures: none happen here but the age refresh."""

    def __init__(self, model: ft.FileTreeModel) -> None:
        super().__init__()
        self.model = model

    @Slot()
    def refreshAges(self) -> None:
        if hasattr(self.model, "refresh_ages"):
            self.model.refresh_ages()

    @Slot(str, result=str)
    def thumbUrl(self, path: str) -> str:
        return ""


class CountingModel(ft.FileTreeModel):
    calls = 0

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        CountingModel.calls += 1
        return super().data(index, role)


def tree(n: int) -> tuple[list[str], dict[str, tuple[int, int]]]:
    rnd = random.Random(115)
    now_ns = time.time_ns()
    folders = 200
    paths: list[str] = []
    stats: dict[str, tuple[int, int]] = {}
    for i in range(n):
        tail = rnd.randrange(10**9)
        p = f"runs/run_{i % folders:03d}/{i:05d}_frame_from_the_front_camera_{tail}.jpg"
        age_s = rnd.choice(
            (
                rnd.uniform(0, 600),
                rnd.uniform(600, 3600),
                rnd.uniform(3600, 86400),
                rnd.uniform(86400, 30 * 86400),
            )
        )
        paths.append(p)
        stats[p] = (rnd.randrange(10_000, 5_000_000), now_ns - int(age_s * 1e9))
    return paths, stats


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, round(p * (len(s) - 1))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, default=20000)
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--warmup", type=float, default=2.0)
    ap.add_argument("--speed", type=float, default=3000.0, help="scroll speed, px/s")
    ap.add_argument("--modes", default="off,on,off,on")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--label", default="", help="a note for the summary (the build measured)")
    args = ap.parse_args()

    prepare(web=False)  # the app's own render setup (OpenGL RHI, Basic controls)
    app = QGuiApplication(sys.argv)
    view = QQuickView()
    view.setTitle("specto bench_file_tree")
    theme = install_qml(view.engine())  # noqa: F841  (keep the context object alive)
    details = Details()
    view.rootContext().setContextProperty("viewSettings", details)
    model = CountingModel()
    paths, stats = tree(args.files)
    t0 = time.perf_counter()
    model.set_paths(paths, stats)
    model.expanded = {n.path for n in model._nodes.values() if n.is_dir}
    model._set_rows(model._walk())
    build_s = time.perf_counter() - t0
    bridge = Bridge(model)
    view.setInitialProperties({"viewId": "bench", "treeModel": model, "bridge": bridge})
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.setSource(QUrl.fromLocalFile(str(ft.FILE_TREE_QML)))
    view.resize(320, 900)
    view.setColor(QColor(COLORS["surface"]))
    view.show()
    root = view.rootObject()
    if root is None:
        print("[ERROR] FileTreeView.qml failed to load")
        return 1
    if hasattr(model, "widest_texts"):  # a build before #115: the size/modified columns
        size, modified = model.widest_texts()
        root.setProperty("sizeSample", size)
        root.setProperty("modifiedSample", modified)
    lst = root.findChild(QQuickItem, "fileTreeList:bench")
    rows = model.rowCount()

    clock = QElapsedTimer()
    clock.start()
    gaps: list[float] = []
    last = {"t": 0.0}

    def on_swapped() -> None:
        now = clock.nsecsElapsed() / 1e6
        if last["t"]:
            gaps.append(now - last["t"])
        last["t"] = now

    view.frameSwapped.connect(on_swapped, Qt.ConnectionType.QueuedConnection)

    pan = {"cy": 0.0, "dir": 1.0, "t": 0.0, "px": 0.0}

    def on_pan() -> None:
        now = clock.nsecsElapsed() / 1e9
        dt = now - pan["t"] if pan["t"] else 0.0
        pan["t"] = now
        top = max(0.0, float(lst.property("contentHeight")) - float(lst.property("height")))
        step = args.speed * dt * pan["dir"]
        cy = pan["cy"] + step
        if cy >= top or cy <= 0:
            pan["dir"] *= -1
            cy = min(max(cy, 0.0), top)
        pan["px"] += abs(cy - pan["cy"])
        pan["cy"] = cy
        lst.setProperty("contentY", cy)

    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setInterval(4)
    timer.timeout.connect(on_pan)

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    results: list[dict] = []
    state: dict = {}

    def start_mode(i: int) -> None:
        if i >= len(modes):
            timer.stop()
            app.quit()
            return
        details.set(modes[i] == "on")
        state.update(i=i)
        QTimer.singleShot(int(args.warmup * 1000), lambda: measure(i))

    def measure(i: int) -> None:
        gaps.clear()
        CountingModel.calls = 0
        pan["px"] = 0.0
        state.update(cpu=time.thread_time(), wall=time.perf_counter())
        QTimer.singleShot(int(args.seconds * 1000), lambda: finish(i))

    def finish(i: int) -> None:
        wall = time.perf_counter() - state["wall"]
        cpu = time.thread_time() - state["cpu"]
        row_h = float(root.property("rowH"))
        xs = list(gaps)
        results.append(
            {
                "mode": modes[i],
                "row_px": row_h,
                "frames": len(xs),
                "fps": round(len(xs) / wall, 1),
                "frame_ms_mean": round(statistics.fmean(xs), 2) if xs else 0,
                "frame_ms_p50": round(percentile(xs, 0.50), 2),
                "frame_ms_p95": round(percentile(xs, 0.95), 2),
                "frame_ms_p99": round(percentile(xs, 0.99), 2),
                "frame_ms_max": round(max(xs, default=0.0), 2),
                "over_20ms_pct": round(100 * sum(1 for x in xs if x > 20) / max(1, len(xs)), 2),
                "gui_busy_pct": round(100 * cpu / wall, 1),
                "rows_per_s": round(pan["px"] / row_h / wall, 1),
                "data_calls_per_s": round(CountingModel.calls / wall),
            }
        )
        r = results[-1]
        print(
            f"[OK] details {r['mode']}: {r['fps']} fps, p50 {r['frame_ms_p50']} ms, "
            f"p99 {r['frame_ms_p99']} ms, max {r['frame_ms_max']} ms, "
            f">20ms {r['over_20ms_pct']}%, GUI {r['gui_busy_pct']}%"
        )
        start_mode(i + 1)

    def go() -> None:
        timer.start()
        start_mode(0)

    QTimer.singleShot(500, go)
    app.exec()

    out = {
        "bench": "file_tree_scroll",
        "label": args.label,
        "files": args.files,
        "rows": rows,
        "model_build_s": round(build_s, 3),
        "speed_px_s": args.speed,
        "seconds": args.seconds,
        "window": [320, 900],
        "platform": platform.platform(),
        "screen_hz": round(view.screen().refreshRate(), 1) if view.screen() else None,
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    args.out.with_suffix(".md").write_text(summary_md(out), encoding="utf-8")
    print(f"[OK] wrote {args.out}")
    return 0


def summary_md(r: dict) -> str:
    lines = [
        f"# File tree scroll, {r['files']} files ({r['rows']} rows)"
        + (f": {r['label']}" if r["label"] else ""),
        "",
        f"Window 320 x 900, scroll {r['speed_px_s']:.0f} px/s, {r['seconds']} s per mode, "
        f"screen {r['screen_hz']} Hz, {r['platform']}.",
        "",
        "| details | row px | fps | p50 ms | p95 ms | p99 ms | max ms | >20 ms | GUI busy "
        "| rows/s | data()/s |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in r["results"]:
        lines.append(
            f"| {x['mode']} | {x['row_px']:.0f} | {x['fps']} | {x['frame_ms_p50']} | "
            f"{x['frame_ms_p95']} | {x['frame_ms_p99']} | {x['frame_ms_max']} | "
            f"{x['over_20ms_pct']}% | {x['gui_busy_pct']}% | {x['rows_per_s']} | "
            f"{x['data_calls_per_s']} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
