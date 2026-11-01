"""Child process of test_mpv_lua.py: open libmpv ``n`` times with faulthandler logging to
a file, then print ``LUA_ERRORS <count>``, the number of 0xE24C4A02 exceptions it logged.
That code is LuaJIT's error unwinding (libmpv's built-in Lua scripts); a count above zero
means libmpv still runs Lua.

    python mpv_lua_child.py poster <clip.mp4> <n>   # thumbs.video_poster
    python mpv_lua_child.py item <n>                # an MpvItem, offscreen
"""

from __future__ import annotations

import faulthandler
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
LUA_ERROR = "0xe24c4a02"


def posters(clip: str, n: int) -> None:
    from specto_app import thumbs

    for _ in range(n):
        assert thumbs.video_poster(clip, 64) is not None


def items(n: int) -> None:
    from PySide6.QtCore import QCoreApplication, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    from specto_app.views import gallery_native

    app = QGuiApplication([])
    engine = QQmlEngine()
    gallery_native.ensure_import_path(engine)
    comp = QQmlComponent(engine)
    comp.setData(b"import QtQuick\nimport Specto.Native\nMpvItem { width: 64; height: 64 }", QUrl())
    assert comp.status() == QQmlComponent.Status.Ready, comp.errorString()
    for _ in range(n):
        item = comp.create()
        assert not item.property("error"), item.property("error")
        end = time.monotonic() + 0.3  # the scripts start on libmpv's own threads
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        item.deleteLater()
        QCoreApplication.processEvents()


def main() -> int:
    log = tempfile.TemporaryFile("w+")
    faulthandler.enable(file=log, all_threads=True)
    if sys.argv[1] == "poster":
        posters(sys.argv[2], int(sys.argv[3]))
    else:
        items(int(sys.argv[2]))
    faulthandler.disable()
    log.seek(0)
    print(f"LUA_ERRORS {log.read().count(LUA_ERROR)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
