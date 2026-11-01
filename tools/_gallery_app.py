"""Shared scaffolding for tools/demo_gallery.py and tools/bench_gallery.py: a real engine
with the worker pool, a canvas document whose galleries are fed by intaker links from
the project source, gallery views hosted in a minimal Qt Quick window (no shell), with
the pool pumped from a timer. Views that appear in the document later get a slot of
their own.

Import this before PySide6: it sets QSG_RHI_BACKEND=opengl (spike S1 decision).
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QSG_RHI_BACKEND", "opengl")

from PySide6.QtCore import QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.hosts import HostContext, HostRegistry
from specto_app.theme import ThemeObject
from specto_app.views import gallery_native
from specto_app.views.gallery import register as register_gallery
from specto_core import Engine
from specto_core.links import Collection
from specto_core.project import Project
from specto_readers import PoolJobManager

TESTDATA = Path(os.environ.get("SPECTO_TESTDATA", Path(__file__).resolve().parents[1] / "testdata"))
CAM_A = TESTDATA / "runs" / "run-001" / "cam-a"
CAM_B = TESTDATA / "runs" / "run-001" / "cam-b"
KEYS = r"^(?P<frame>\d{5})_"

WINDOW_QML = b"""
import QtQuick
import QtQuick.Window

Window {
    id: win
    width: 1600
    height: 900
    visible: true
    color: theme.colors.bg
    title: windowTitle
    Row {
        id: row
        anchors.fill: parent
        anchors.margins: 12
        spacing: 12
        Repeater {
            model: slots
            Rectangle {
                required property var modelData
                width: (row.width - row.spacing * (slots.length - 1)) / slots.length
                height: row.height
                radius: theme.metrics.radius_card
                color: theme.colors.surface
                border.color: theme.colors.border
                border.width: 1
                Text {
                    id: head
                    x: 12; y: 6
                    text: modelData.label
                    font.pixelSize: theme.metrics.font_px
                    font.family: theme.fonts.ui
                    color: theme.colors.text_muted
                }
                Item {
                    objectName: "slot_" + modelData.id
                    x: 6
                    y: theme.metrics.header_h
                    width: parent.width - 12
                    height: parent.height - theme.metrics.header_h - 6
                    clip: true
                }
            }
        }
    }
}
"""


def raise_timer_resolution() -> None:
    """Qt 6 no longer raises the Windows timer resolution (spike gotcha)."""
    if sys.platform == "win32":
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception as e:  # pragma: no cover
            print(f"[WARN] timeBeginPeriod failed: {e}")


def require_plugin() -> None:
    if not gallery_native.is_built():
        sys.exit(f"[ERROR] Specto.Native is not built; {gallery_native.BUILD_HINT}")


def make_engine(
    workers: int | None = None, project: Path | None = None
) -> tuple[Engine, PoolJobManager]:
    """The engine over the worker pool; ``project`` opens (or makes) a project there, so
    intakers see its files as ``project.all_files``."""
    pool = PoolJobManager(workers=workers)
    proj = Project.open_or_init(project) if project is not None else None
    engine = Engine(project=proj, jobs=pool)
    return engine, pool


def wait_jobs(engine: Engine, pool: PoolJobManager, timeout: float = 600) -> None:
    """Block until every active pool job (the link resolves) finished, then deliver."""
    while ids := [j.id for j in pool.list(active_only=True)]:
        # a resolve that lands can start the next one (project files, then the intakers)
        if not pool.wait(ids, timeout):
            sys.exit("[ERROR] resolve jobs timed out")
        pool.poll(0)
        for jid in ids:
            j = pool.status(jid)
            if j.state != "done":
                sys.exit(f"[ERROR] job {jid} {j.state}: {j.error}")


def build_canvas(
    engine: Engine,
    pool: PoolJobManager,
    collections: list[tuple[str, list[str]]],
    *,
    link_selection: bool = True,
    tile: int = 0,
    screener: list[str] | None = None,
) -> list[str]:
    """One gallery per ``(label, [roots])``, each fed by an intaker from the project
    source over those (absolute) roots, the galleries following each other's selection
    through two-way follow links; ``screener`` replaces the default ``["@images"]``
    (``[]`` takes every file). Waits for the resolves. Returns the view ids."""
    doc = engine.live.document()
    doc["views"], doc["links"] = {}, {}  # the tool shows galleries only, no file tree
    n = len(collections)
    views: list[str] = []
    for i, (label, roots) in enumerate(collections, start=1):
        vid = f"v{i}"
        views.append(vid)
        w = (1600 - 24 - 12 * (n - 1)) / n
        doc["views"][vid] = {
            "type": "gallery",
            "label": label,
            "layout": {"position": [(i - 1) * (w + 12), 0], "size": [w, 860]},
            "params": {"tile": tile} if tile > 0 else {},
        }
        doc["links"][f"l{i}"] = {
            "kind": "intaker",
            "label": label,
            "scope": "pinned",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": vid, "port": "data"},
            "params": {
                "sources": [str(Path(r)) for r in roots],
                "screener": ["@images"] if screener is None else screener,
                "keys": KEYS,
                "group_by": ["frame"],
            },
            "summary": {"sentence": f"The {label} gallery shows the images under its roots."},
        }
    if link_selection and len(views) >= 2:
        for i in range(1, len(views)):
            doc["links"][f"l{n + i}"] = {
                "kind": "follow",
                "two_way": True,
                "source": {"view": views[0], "port": "selection"},
                "target": {"view": views[i], "port": "selection"},
                "summary": {
                    "sentence": "Selecting a frame in one gallery selects it in the other."
                },
            }
    t0 = time.perf_counter()
    engine.live.submit(doc, origin="user", label="gallery tool")
    views = list(engine.live.data["views"])  # the submit names the views (pigi, lami...)
    links = engine.links  # starts the resolves on the pool
    wait_jobs(engine, pool)
    print(f"[OK] resolved {n} intaker(s) in {time.perf_counter() - t0:.2f} s")
    for vid, (label, _roots) in zip(views, collections, strict=True):
        col = links.input_of(vid, "data")
        count = len(col.items) if isinstance(col, Collection) else 0
        print(f"[OK] {vid} ({label}): {count} items")
    return views


def find_item(root: QQuickItem, object_name: str) -> QQuickItem | None:
    """Repeater delegates have no QObject parent chain, so findChild misses them: walk
    the item tree instead."""
    for child in root.childItems():
        if child.objectName() == object_name:
            return child
        hit = find_item(child, object_name)
        if hit is not None:
            return hit
    return None


def pick_screen(app: QGuiApplication, index: int | None = None):
    """``index`` if given, else the 1920x1080 ~60 Hz screen (spike report), else primary."""
    screens = app.screens()
    if index is not None and 0 <= index < len(screens):
        return screens[index]
    for s in screens:
        sz = s.size()
        if (sz.width(), sz.height()) == (1920, 1080) and abs(s.refreshRate() - 60) < 1.5:
            return s
    return app.primaryScreen()


class GalleryWindow(QObject):
    """A window with one slot per view, the host registry attached to the slots, and the
    pool poll timer every shell needs. New views in the document get a slot on the next
    event-loop turn."""

    def __init__(
        self,
        app: QGuiApplication,
        engine: Engine,
        pool: PoolJobManager,
        view_ids: list[str],
        *,
        title: str = "Specto gallery",
        screen=None,
        size: tuple[int, int] = (1600, 900),
    ) -> None:
        super().__init__()
        self.app = app
        self.engine = engine
        self.pool = pool
        self.view_ids = list(view_ids)
        self.qml = QQmlEngine()
        gallery_native.ensure_import_path(self.qml)
        self.theme = ThemeObject()
        ctx_root = self.qml.rootContext()
        ctx_root.setContextProperty("theme", self.theme)
        ctx_root.setContextProperty("slots", self._slots())
        ctx_root.setContextProperty("windowTitle", title)
        self.component = QQmlComponent(self.qml)
        self.component.setData(WINDOW_QML, QUrl())
        if self.component.isError():
            sys.exit(f"[ERROR] window QML: {self.component.errorString()}")
        self.window: QQuickWindow = self.component.create()
        if screen is not None:
            self.window.setScreen(screen)
            g = screen.geometry()
            self.window.setPosition(g.x() + 40, g.y() + 40)
        self.window.resize(*size)

        self.ctx = HostContext(engine, self.qml)
        self.registry = HostRegistry(self.ctx, load_entry_points=False)
        register_gallery(self.registry)
        self._off_patch = engine.live.on_patch(self._on_patch)
        self.hosts: dict[str, Any] = {}
        self._slot_of: dict[str, QQuickItem] = {}
        self._attach_all()

        self.pump = QTimer(self)
        self.pump.setTimerType(Qt.TimerType.PreciseTimer)
        self.pump.setInterval(4)
        self.pump.timeout.connect(self._pump)
        self.pump.start()

    def _slots(self) -> list[dict[str, str]]:
        views = self.engine.live.data["views"]
        return [{"id": v, "label": f"{views[v].get('label', v)}  {v}"} for v in self.view_ids]

    def _attach_all(self) -> None:
        for v in self.view_ids:
            if v in self.hosts:
                continue
            slot = find_item(self.window.contentItem(), f"slot_{v}")
            if slot is None or not self.registry.attach(v, slot):
                sys.exit(f"[ERROR] cannot attach {v}")
            self._slot_of[v] = slot
            self.hosts[v] = self.registry.host(v)

    def _on_patch(self, patch: list[dict], meta: dict) -> None:
        live = list(self.engine.live.data["views"])
        added = [v for v in live if v not in self.view_ids]
        removed = [v for v in self.view_ids if v not in live]
        if not added and not removed:
            return
        for v in removed:
            self.hosts.pop(v, None)
            self._slot_of.pop(v, None)
            self.view_ids.remove(v)
        self.view_ids.extend(added)
        # the Repeater rebuilds its delegates: park every item, re-attach on the next turn
        for v, slot in list(self._slot_of.items()):
            self.registry.detach(v, slot)
        self._slot_of.clear()
        self.hosts.clear()
        self.qml.rootContext().setContextProperty("slots", self._slots())
        QTimer.singleShot(0, self._attach_all)

    def _pump(self) -> None:
        self.pool.poll(0)

    def gallery_item(self, view_id: str) -> QQuickItem:
        return self.hosts[view_id].gallery.item

    def close(self) -> None:
        self.pump.stop()
        self._off_patch()
        self.registry.dispose_all()
        self.window.close()
        self.pool.shutdown()
