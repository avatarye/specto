"""Child process of test_video_orientation.py: play a clip paused on its first frame in
an MpvItem inside a small real window (libmpv renders through OpenGL, which the offscreen
platform has no context for on Windows), grab it and print the top- and bottom-centre
pixels as JSON. The window takes no focus and lets input through; the parent holds the
desktop lock while it shows.

    python video_orient_child.py <clip.mp4> [grab.png]
"""

from __future__ import annotations

import json
import os
import sys
import time

os.environ["QSG_RHI_BACKEND"] = "opengl"
os.environ.pop("QT_QUICK_BACKEND", None)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickView

from specto_app.views import gallery_native

SIZE = 128


def main() -> int:
    clip = sys.argv[1]
    app = QGuiApplication([])
    view = QQuickView()
    gallery_native.ensure_import_path(view.engine())
    comp = QQmlComponent(view.engine())
    comp.setData(
        b"import QtQuick\nimport Specto.Native\nMpvItem { width: 128; height: 128 }", QUrl()
    )
    assert comp.status() == QQmlComponent.Status.Ready, comp.errorString()
    item = comp.create()
    assert isinstance(item, QQuickItem)
    item.setParentItem(view.contentItem())
    view.resize(SIZE, SIZE)
    view.setFlags(
        Qt.WindowType.Tool
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowDoesNotAcceptFocus
        | Qt.WindowType.WindowTransparentForInput
    )
    view.setPosition(40, 40)
    view.show()
    item.setProperty("source", clip)
    item.setProperty("paused", True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        app.processEvents()
        if int(item.property("renderFrames") or 0) >= 2:
            break
        time.sleep(0.01)
    # a couple more frames so the grab sees the decoded picture, not the clear colour
    for _ in range(10):
        app.processEvents()
        time.sleep(0.02)
    img = view.grabWindow()
    out = {
        "ready": bool(item.property("ready")),
        "backend": str(item.property("backend")),
        "error": str(item.property("error")),
        "renderFrames": int(item.property("renderFrames") or 0),
        "top": img.pixelColor(SIZE // 2, SIZE // 8).name(),
        "bottom": img.pixelColor(SIZE // 2, SIZE - SIZE // 8).name(),
    }
    if len(sys.argv) > 2:
        img.save(sys.argv[2])
    view.hide()
    print("RESULT " + json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
