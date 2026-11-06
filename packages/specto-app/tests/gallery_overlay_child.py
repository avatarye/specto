"""Child process of test_gallery_overlay.py: a row of native GalleryItem tiles drawn by the
real scene graph (OpenGL; the offscreen software renderer draws no gallery tiles), in a
tool window parked far off every monitor that never takes the focus. One scenario per
run (SCENARIOS); prints one JSON line: the graphics API, the device pixel ratio, each
tile's thumb rect and drawn caption, the caption band's height and the grab's path.
Tile 1 is always the selected one, tile 2 the current one.

    python -I gallery_overlay_child.py <scenario> <grab.png>
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
WHITE, BLACK = 255, 0
#: dense glyphs, so the band has plenty of text pixels to measure
KEY = "MWMWMW"
LONG = "frame_0001234567_cam_front_left_rectified_MWMWMW"
NORMAL = (150, 100)  # the cell's own aspect (1.5)
WBW = [(WHITE, *NORMAL), (BLACK, *NORMAL), (WHITE, *NORMAL)]
#: name -> tile width, caption mode, tiles (grey value, w, h), and keys / names
SCENARIOS: dict[str, dict] = {
    "key": {"tile": 150, "mode": 1, "tiles": WBW},
    "key_and_name": {"tile": 150, "mode": 2, "tiles": WBW, "names": ["MWMW.jpg"] * 3},
    "off": {"tile": 150, "mode": 0, "tiles": WBW},
    "long_key": {"tile": 150, "mode": 1, "tiles": WBW, "keys": [LONG] * 3},
    # a panorama (10:1) before two normal frames: a thumb too short for a caption
    "panorama": {"tile": 150, "mode": 1, "tiles": [(WHITE, 600, 60), *WBW[1:]]},
    # small tiles, wide enough for a caption (>= captionMinWidth) but too short for it
    "small": {"tile": 76, "mode": 1, "tiles": WBW},
}


def solid(value: int, w: int, h: int) -> QByteArray:
    return QByteArray(bytes([value, value, value, 255]) * (w * h))


def main() -> int:
    sc = SCENARIOS[sys.argv[1]]
    out = sys.argv[2]
    n = len(sc["tiles"])
    width, height = n * sc["tile"] + (n - 1) * 8 + 16, int(sc["tile"] / 1.5) + 40
    app = QGuiApplication(["gallery-overlay"])
    view = QQuickView()
    gallery_native.ensure_import_path(view.engine())
    comp = QQmlComponent(view.engine())
    qml = (
        "import QtQuick\nimport Specto.Native\n"
        f"GalleryItem {{ width: {width}; height: {height}; columns: {n}; gap: 8; margin: 8; "
        f"autoAspect: false; cellAspect: 1.5; captionMode: {sc['mode']}; radius: 6; "
        "borderWidth: 3 }"
    )
    comp.setData(qml.encode(), QUrl())
    if comp.status() != QQmlComponent.Status.Ready:
        print(json.dumps({"error": comp.errorString()}))
        return 1
    g = comp.create()
    assert isinstance(g, QQuickItem)
    g.setParentItem(view.contentItem())
    view.resize(width, height)
    view.setColor(Qt.GlobalColor.white)
    view.setFlags(
        Qt.WindowType.Tool
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowDoesNotAcceptFocus
        | Qt.WindowType.WindowTransparentForInput
    )
    view.setPosition(PARKED, PARKED)
    g.setProperty("count", n)
    g.setCaptions(sc.get("keys", [KEY] * n), sc.get("names", [""] * n))
    for i, (v, w, h) in enumerate(sc["tiles"]):
        g.pushThumb(i, solid(v, w, h), w, h)
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
    for i in range(n):
        r = g.rectOf(i)
        rects.append([r.x(), r.y(), r.width(), r.height()])
    print(
        json.dumps(
            {
                "api": api,
                "dpr": img.devicePixelRatio(),
                "rects": rects,
                "drawn": [g.drawnCaption(i) for i in range(n)],
                "band": g.property("captionHeight"),
                "resident": g.property("residentFull") + g.property("residentLarge"),
                "grab": out,
            }
        )
    )
    view.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
