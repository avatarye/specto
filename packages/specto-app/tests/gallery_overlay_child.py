"""Child process of test_gallery_overlay.py: three tiles of a native GalleryItem drawn by
the real scene graph (OpenGL; the offscreen software renderer draws no gallery tiles), in
a tool window parked far off every monitor that never takes the focus. Tile 0 is a white
image, tile 1 a black one (selected), tile 2 white again (the current item). Prints one
JSON line: the graphics API, the device pixel ratio, each tile's thumb rect, the caption
band's height and the grab's path.

    python -I gallery_overlay_child.py <grab.png>
"""

from __future__ import annotations

import json
import os
import sys
import time

os.environ.pop("QT_QUICK_BACKEND", None)
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ["QSG_RHI_BACKEND"] = "opengl"

from PySide6.QtCore import QByteArray, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickView

from specto_app.views import gallery_native

PARKED = -12000
W, H = 3 * 150 + 2 * 8 + 16, 160
#: dense glyphs, so the band has plenty of text pixels to measure
KEYS = ["MWMWMW", "MWMWMW", "MWMWMW"]
QML = (
    b"import QtQuick\nimport Specto.Native\n"
    b"GalleryItem { width: %d; height: %d; columns: 3; gap: 8; margin: 8; autoAspect: false; "
    b"cellAspect: 1.5; captionMode: 1; radius: 6; borderWidth: 3 }" % (W, H)
)


def solid(value: int, w: int = 150, h: int = 100) -> QByteArray:
    return QByteArray(bytes([value, value, value, 255]) * (w * h))


def main() -> int:
    out = sys.argv[1]
    app = QGuiApplication(["gallery-overlay"])
    view = QQuickView()
    gallery_native.ensure_import_path(view.engine())
    comp = QQmlComponent(view.engine())
    comp.setData(QML, QUrl())
    if comp.status() != QQmlComponent.Status.Ready:
        print(json.dumps({"error": comp.errorString()}))
        return 1
    g = comp.create()
    assert isinstance(g, QQuickItem)
    g.setParentItem(view.contentItem())
    view.resize(W, H)
    view.setColor(Qt.GlobalColor.white)
    view.setFlags(
        Qt.WindowType.Tool
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowDoesNotAcceptFocus
        | Qt.WindowType.WindowTransparentForInput
    )
    view.setPosition(PARKED, PARKED)
    g.setProperty("count", 3)
    g.setCaptions(KEYS, ["", "", ""])
    for i, v in enumerate((255, 0, 255)):
        g.pushThumb(i, solid(v), 150, 100)
    g.setProperty("selectedIndex", 1)
    g.setProperty("currentIndex", 2)
    view.show()
    # uploads land in the first syncs; the tiles fade in over 180 ms: draw a while
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        g.update()
        app.processEvents()
        time.sleep(0.01)
    img = view.grabWindow()
    img.save(out)
    api = str(view.rendererInterface().graphicsApi()).rsplit(".", 1)[-1]
    rects = []
    for i in range(3):
        r = g.rectOf(i)
        rects.append([r.x(), r.y(), r.width(), r.height()])
    print(
        json.dumps(
            {
                "api": api,
                "dpr": img.devicePixelRatio(),
                "rects": rects,
                "band": g.property("captionHeight"),
                "resident": g.property("residentFull"),
                "captions": g.property("captions"),
                "grab": out,
            }
        )
    )
    view.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
