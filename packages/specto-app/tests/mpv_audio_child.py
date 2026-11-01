"""Child process of test_mpv_audio.py: an MpvItem offscreen (no file, so no audio device
is opened), printing its sound properties as JSON before and after the host-side writes:
``RESULT {"before": {...}, "after": {...}}``. ``options/ao`` and ``options/aid`` are the
core's options (#216: the platform's output, the first audio track); ``mute`` and
``volume`` the live properties.

    python mpv_audio_child.py
"""

from __future__ import annotations

import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def state(item) -> dict:
    return {
        "ready": bool(item.property("ready")),
        "error": str(item.property("error")),
        "muted": bool(item.property("muted")),
        "volume": float(item.property("volume")),
        "hasAudio": bool(item.property("hasAudio")),
        "mute": item.mpvProperty("mute"),
        "mpv_volume": item.mpvProperty("volume"),
        "ao": item.mpvProperty("options/ao"),
        "aid": item.mpvProperty("options/aid"),
        "video_sync": item.mpvProperty("options/video-sync"),
        "hr_seek": item.mpvProperty("options/hr-seek"),
    }


def spin(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    from specto_app.views import gallery_native

    app = QGuiApplication([])
    engine = QQmlEngine()
    gallery_native.ensure_import_path(engine)
    comp = QQmlComponent(engine)
    comp.setData(b"import QtQuick\nimport Specto.Native\nMpvItem { width: 64; height: 64 }", QUrl())
    assert comp.status() == QQmlComponent.Status.Ready, comp.errorString()
    item = comp.create()
    spin(app, 0.3)
    before = state(item)
    item.setProperty("muted", False)
    item.setProperty("volume", 40.0)
    spin(app, 0.5)
    after = state(item)
    item.deleteLater()
    spin(app, 0.1)
    print("RESULT " + json.dumps({"before": before, "after": after}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
