"""A drop target outside Specto for seam-3 drag flows (#185): a small always-on-top window
at the primary screen's right edge that takes any drop and records what it was given.

    uv run python tools/gui_flows/drop_probe.py <state.json> [seconds]

``state.json`` holds ``{"ready": true, "rect": [x, y, w, h], "drops": [...]}``: the window
in physical screen pixels (where ``gui_drive dragout`` aims) and one entry per drop: its
formats, the Specto payload (``application/x-specto-item+json``, parsed), the text, the
URLs and the size of any ``image/png``. Read in the order drop targets read it (the drag
contract). The file appears only once the window is ready (#195): shown and exposed,
registered as a drop target, and at the same place on two checks in a row, so a drag
aimed at ``rect`` lands on it; ``dragout`` waits for it. The window closes when
``<state>.quit`` appears (``gui_drive close_probe``, and gui_drive on exit for every probe
it dragged into, pass or fail) or after ``seconds`` (default 120) at the latest: flows
never kill it, and no probe outlives its run.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QWidget

SPECTO = "application/x-specto-item+json"


class Probe(QLabel):
    def __init__(self, state: Path) -> None:
        super().__init__("drop here")
        self.state = state
        self.drops: list[dict] = []
        self.setAcceptDrops(True)
        self.setWindowTitle("specto drop probe")
        self.setStyleSheet("background: #203040; color: #e0e8f0; font: 14px;")
        self.setMinimumSize(240, 240)
        # above the app's window, which gui_drive brings to the front before each gesture
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.ready = False
        self._last: list[int] | None = None
        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self._check)

    def place(self) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - 260, geo.top() + 80)
        self._poll.start()

    def rect_px(self) -> list[int]:
        dpr = self.devicePixelRatioF()
        top_left = self.mapToGlobal(self.rect().topLeft())
        return [round(v * dpr) for v in (top_left.x(), top_left.y(), self.width(), self.height())]

    def _check(self) -> None:
        """Ready once the window is exposed, a drop target, and has stopped moving."""
        handle = self.windowHandle()
        if handle is None or not handle.isExposed() or not self.acceptDrops():
            self._last = None
            return
        rect = self.rect_px()
        if rect != self._last:
            self._last = rect
            return
        self._poll.stop()
        self.ready = True
        self.write()
        print(f"[OK] drop probe ready at {rect}", flush=True)

    def write(self) -> None:
        if not self.ready:
            return
        tmp = self.state.with_suffix(".tmp")
        state = {"ready": True, "rect": self.rect_px(), "drops": self.drops}
        tmp.write_text(json.dumps(state, indent=1), "utf-8")
        os.replace(tmp, self.state)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        md = event.mimeData()
        specto = None
        if md.hasFormat(SPECTO):
            specto = json.loads(bytes(md.data(SPECTO).data()).decode("utf-8"))
        self.drops.append(
            {
                "formats": list(md.formats()),
                "specto": specto,
                "text": md.text() if md.hasText() else None,
                "urls": [u.toString() for u in md.urls()],
                "png": len(bytes(md.data("image/png").data())) if md.hasFormat("image/png") else 0,
            }
        )
        self.setText(f"{len(self.drops)} drop(s)")
        event.acceptProposedAction()
        self.write()
        print(f"[OK] drop {len(self.drops)}: {md.formats()}", flush=True)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    state = Path(sys.argv[1]).resolve()
    quit_file = state.with_suffix(".quit")
    state.unlink(missing_ok=True)  # a last run's files are not this window's
    quit_file.unlink(missing_ok=True)
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    app = QApplication(sys.argv[:1])
    probe = Probe(state)
    probe.show()
    probe.raise_()
    probe.place()
    QTimer.singleShot(seconds * 1000, app.quit)

    def quit_when_asked() -> None:
        if quit_file.exists():
            quit_file.unlink(missing_ok=True)
            print("[OK] drop probe closed", flush=True)
            app.quit()

    poll = QTimer()
    poll.setInterval(200)
    poll.timeout.connect(quit_when_asked)
    poll.start()
    keep: QWidget = probe  # noqa: F841
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
