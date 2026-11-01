"""Child process of test_image_channels.py: ``ImageView.qml`` rendered with OpenGL into an
offscreen FBO (no window on screen), showing one known PNG with each ``params.channels``
of the plan (the uniforms from ``views.channels.ChannelView``, as the host pushes them);
prints one ``RESULT {json}`` line: per case, the RGB at the plan's sample points of the
viewport, the image's status and the effect's geometry.

    python image_channels_child.py '{"png": "known.png", "cases": [...], "points": [...]}'
"""

from __future__ import annotations

import json
import sys
import time

from specto_app.frames.offscreen import OffscreenQuick, prepare_environment

prepare_environment()

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlComponent  # noqa: E402

from specto_app.qt_setup import install_qml  # noqa: E402
from specto_app.views.channels import ChannelView, probe  # noqa: E402
from specto_app.views.image_view import IMAGE_QML  # noqa: E402

W, H = 256, 192  # the footer takes 24 px: a 256 x 168 viewport


def main() -> int:
    plan = json.loads(sys.argv[1])
    app = QGuiApplication([])
    quick = OffscreenQuick(W, H)
    theme = install_qml(quick.engine)
    comp = QQmlComponent(quick.engine, QUrl.fromLocalFile(str(IMAGE_QML)))
    item = comp.createWithInitialProperties({"viewId": "tamu"})
    if item is None:
        print("ERROR", comp.errorString())
        return 1
    item.setParentItem(quick.window.contentItem())
    item.setSize(quick.window.contentItem().size())
    item.setProperty("source", QUrl.fromLocalFile(plan["png"]))
    view = ChannelView(probe(plan["png"]))

    def frames(n: int = 4, until=None) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for _ in range(n):
                app.processEvents()
                quick.render()
            if until is None or until():
                return
        raise SystemExit("timed out")

    frames(until=lambda: item.property("status") == "ready")
    effect = next(c for c in _all(item) if c.objectName() == "channels:tamu")
    out: dict = {"cases": [], "gpu": item.property("gpu"), "theme": bool(theme)}
    for case in plan["cases"]:
        view.set_param(case.get("channels", ""))
        item.setProperty("channels", {**view.snapshot(), **view.uniforms()})
        if "zoom" in case:
            item.setProperty("zoom", case["zoom"])
        frames()
        img = quick.grab()
        px = [list(img.pixelColor(x, y).getRgb()[:3]) for x, y in plan["points"]]
        out["cases"].append(
            {
                "channels": case.get("channels", ""),
                "zoom": item.property("zoom"),
                "pixels": px,
                "status": item.property("status"),
                "effect": [effect.isVisible(), effect.x(), effect.y(), effect.width()],
            }
        )
    quick.close()
    print("RESULT " + json.dumps(out))
    return 0


def _all(item):
    stack = [item]
    while stack:
        it = stack.pop()
        yield it
        stack.extend(it.childItems())


if __name__ == "__main__":
    sys.exit(main())
