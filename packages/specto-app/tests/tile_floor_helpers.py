"""Stand-ins for what ``TileFloor`` reads from the app (#191): the canvas controller, the
prompt bar's agent icon, a clock the test steps."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal


class Controller(QObject):
    """What TileFloor reads from the canvas controller."""

    viewIdsChanged = Signal()
    viewsChanged = Signal()
    selectionChanged = Signal()
    dragRectsChanged = Signal()
    wiringChanged = Signal()

    def __init__(self, w: float = 1280, h: float = 742) -> None:
        super().__init__()
        self.size = (float(w), float(h))
        self._ids: list[dict] = []
        self._sel: list[str] = []
        self._wiring = False
        self._drag: dict = {}
        # the views shown elsewhere than their document rect (#208): id -> (rect, space)
        self.shown: dict[str, tuple[tuple[float, float, float, float], str]] = {}
        self.jumped: list[str] = []

    def shown_rect(self, vid: str) -> tuple[tuple[float, float, float, float], str]:
        if vid in self.shown:
            return self.shown[vid]
        (e,) = [e for e in self._ids if e["id"] == vid]
        return ((e["x"], e["y"], e["w"], e["h"]), "world")

    @Property("QVariantList", notify=viewIdsChanged)
    def viewIds(self):
        return self._ids

    @Property("QVariantList", notify=selectionChanged)
    def selectedViews(self):
        return self._sel

    @Property("QVariantMap", notify=dragRectsChanged)
    def dragRects(self):
        return self._drag

    @Property(bool, notify=wiringChanged)
    def wiring(self):
        return self._wiring

    def viewport_rect(self):
        return (0.0, 0.0, *self.size)

    def chain_of(self, vid: str) -> str:
        return "chain_0"

    def jumpTo(self, vid: str) -> bool:
        self.jumped.append(vid)
        return True


class Icon(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.state = "idle"


class Bar(QObject):
    turnFinished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.icon = Icon()


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


class Overworld(QObject):
    activeChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.active = False
