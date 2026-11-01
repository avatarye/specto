"""Child process for test_frame_corners: the shell in a window the real render loop
draws (Direct3D 11 or OpenGL through the RHI), parked far off every monitor as a tool
window that never activates, so shader effects (the frame's shadow) and the scene-graph
clip render as on the user's screen while nothing shows on the desktop. The pytest
process itself is pinned to the offscreen platform, whose software renderer draws
neither.

For each view type named on the command line: a solid red view behind the corners of the
view under test; one JSON line per type with the pixels just outside the rounded arc at
each corner and one inside the content, plus the graphics API."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.pop("QT_QUICK_BACKEND", None)
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ.setdefault("QSG_RHI_BACKEND", "d3d11" if sys.platform == "win32" else "opengl")

from PySide6.QtCore import Qt
from PySide6.QtQml import QQmlApplicationEngine

import specto_app.app as appmod

PARKED = -12000


class ParkedEngine(QQmlApplicationEngine):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setInitialProperties({"visible": True, "x": PARKED, "y": PARKED, "flags": int(flags)})


appmod.QQmlApplicationEngine = ParkedEngine

from specto_app.qt_setup import prepare  # noqa: E402

WEB = prepare(web=os.environ.get("FRAME_CORNERS_WEB", "1") == "1", offscreen=False)

from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPdfWriter  # noqa: E402

qapp = QGuiApplication(["frame-corners"])

from specto_app.app import SpectoApp, find_item  # noqa: E402
from specto_app.hosts.base import QuickViewHost, SimpleFactory  # noqa: E402
from specto_app.theme import METRICS  # noqa: E402
from specto_core import CallContext, Project  # noqa: E402

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
R = METRICS["radius_card"]
RECT = {"position": [300, 100], "size": [640, 400]}
# the red view's content (below its own header) lies 20 px past every edge of RECT
BEHIND = {"position": [280, 20], "size": [680, 500]}
SWATCH = "#ff0000"


def spin(ms: float = 60, until=None, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + (timeout if until else ms / 1000)
    while time.monotonic() < deadline:
        qapp.processEvents()
        if until is not None and until():
            break
        time.sleep(0.004)
    qapp.processEvents()
    if until is not None:
        assert until(), "timed out"


class SwatchHost(QuickViewHost):
    """A solid red item: what lies behind the view under test."""

    def __init__(self, ctx, view_id, manifest):
        super().__init__(ctx, view_id, manifest)
        qml = Path(ctx.engine.project.root) / "swatch_view.qml"
        qml.write_text("import QtQuick\nRectangle { }\n", encoding="utf-8")
        self.item = ctx.create_item(qml, properties={"color": SWATCH})


def seed(root: Path) -> None:
    (root / "pics").mkdir(exist_ok=True)
    img = QImage(16, 16, QImage.Format.Format_RGB32)
    img.fill(QColor("#4080c0"))
    for name in ("a.png", "b.png"):
        img.save(str(root / "pics" / name))
    # a two-page pdf for the pdf view (the process runs with -I: no tests/ imports)
    writer = QPdfWriter(str(root / "pics" / "a.pdf"))
    painter = QPainter(writer)
    for i in range(2):
        if i:
            writer.newPage()
        painter.fillRect(0, 0, writer.width(), writer.height() // 3, QColor("#4080c0"))
    painter.end()
    (root / "notes.md").write_text("# note\n\nhello\n", encoding="utf-8")
    (root / "data.json").write_text('{"a": 1}', encoding="utf-8")


def link(vid: str, vtype: str) -> dict:
    out = {
        "kind": "intaker",
        "scope": "pinned",
        "label": "files",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": vid, "port": "data"},
        "params": {"sources": ["pics"] if vtype in ("gallery", "image", "video", "pdf") else ["."]},
        "summary": {"sentence": "The view shows the project's files."},
    }
    if vtype == "map":  # features come through a read_contents link (no scope)
        out["kind"] = "read_contents"
        out.pop("scope")
        out["target"]["port"] = "features"
    return out


def pixel(image: QImage, x: float, y: float) -> str:
    return QColor(image.pixel(int(x), int(y))).name()


def probe(vtype: str) -> dict:
    root = Path(tempfile.mkdtemp(prefix=f"frame-corners-{vtype}-"))
    project = Project.init(root / "proj", "demo")
    app = SpectoApp(project, jobs="inline", web=WEB, offscreen=False, serve=False)
    app.start()
    spin(50)
    try:
        seed(app.project.root)
        # the swatch stands in for a built-in type the view under test is not
        swatch_type = "image" if vtype != "image" else "gallery"
        app.hosts.add_factory(SimpleFactory(swatch_type, SwatchHost))
        doc = app.live.document()
        doc["views"]["lito"] = {"type": swatch_type, "label": "behind", "layout": dict(BEHIND)}
        doc["links"]["l3"] = link("lito", swatch_type)
        vid = "fege" if vtype == "file_tree" else "kobi"
        if vtype == "file_tree":
            doc["views"]["fege"]["layout"] = dict(RECT)
        else:
            doc["views"][vid] = {"type": vtype, "label": vtype, "layout": dict(RECT)}
            doc["links"]["l2"] = link(vid, vtype)
        app.engine.call("canvas.submit", {"document": doc, "label": vtype}, AGENT)
        spin(until=lambda: vid in app.hosts.hosts and "lito" in app.hosts.hosts)
        app.controller.focusView(vid)  # on top of the swatch
        spin(1500 if vtype in ("json", "markdown", "map", "html", "3d") else 500)
        frame = find_item(app.window.contentItem(), f"frame:{vid}")
        p = frame.mapToScene(frame.boundingRect().topLeft())
        fx, fy, fw, fh = p.x(), p.y(), frame.width(), frame.height()
        image = app.window.grabWindow()
        spin(200)
        image = app.window.grabWindow()
        if os.environ.get("FRAME_CORNERS_SAVE"):
            image.save(os.path.join(os.environ["FRAME_CORNERS_SAVE"], f"frame-corners-{vtype}.png"))
        d = 1.5
        api = app.window.rendererInterface().graphicsApi()
        return {
            "type": vtype,
            "host": type(app.hosts.hosts.get(vid)).__name__,
            "api": str(api).split(".")[-1],
            "outside": {
                "top-left": pixel(image, fx + d, fy + d),
                "top-right": pixel(image, fx + fw - d, fy + d),
                "bottom-left": pixel(image, fx + d, fy + fh - d),
                "bottom-right": pixel(image, fx + fw - d, fy + fh - d),
            },
            "inside": pixel(image, fx + R + 6, fy + fh - R - 6),
            "behind": pixel(image, fx - 10, fy + fh + 10),  # the swatch beside the frame
        }
    finally:
        app.close()
        spin(20)


if __name__ == "__main__":
    for t in sys.argv[1:]:
        try:
            print(json.dumps(probe(t)), flush=True)
        except Exception as e:  # one type failing must not hide the others
            print(json.dumps({"type": t, "error": f"{type(e).__name__}: {e}"}), flush=True)
