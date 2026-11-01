"""The usd view's camera tick (#138): 60 Hz on the GUI thread, and only while the camera
flies (RMB and a fly key held, #151): the held keys move it every frame, whatever the
keyboard's repeat does. Drags, the wheel and jumps move the camera at once and are handed
over by ``kick``. Each change becomes one ``set_camera`` to the source."""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Slot

from .nav import CameraState, Nav

TICK_MS = 16  # 60 Hz

OnChange = Callable[[CameraState, bool], None]


class NavTicker(QObject):
    """Drives ``nav.tick`` while it moves and hands each change to ``on_change(state,
    cut)``. The host calls ``kick()`` after every input or jump."""

    def __init__(
        self,
        nav: Nav,
        on_change: OnChange,
        clock: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.nav = nav
        self._on_change = on_change
        self._clock = clock
        self._last = 0.0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    @property
    def interval_ms(self) -> int:
        return self._timer.interval()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def kick(self) -> None:
        """Input arrived or the camera jumped: hand over a jump now, and tick while the
        camera moves."""
        self._flush()
        if self.nav.moving and not self._timer.isActive():
            self._last = self._clock()
            self._timer.start()

    def stop(self) -> None:
        """The view paused or lost focus: no ticks, no key or button held."""
        self._timer.stop()
        self.nav.stop()

    @Slot()
    def _tick(self) -> None:
        now = self._clock()
        dt, self._last = now - self._last, now
        self.nav.tick(dt)
        self._flush()
        if not self.nav.moving:
            self._timer.stop()

    def _flush(self) -> None:
        change = self.nav.take_change()
        if change is not None:
            self._on_change(*change)
