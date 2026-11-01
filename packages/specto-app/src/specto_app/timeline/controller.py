"""``TimelineController``: the model behind the video footer's progress bar
(``Specto/Video/VideoProgress.qml``) and its frame readout (``label``).

One controller per bar. It holds the range, the cursor ``t``, the hover time, the tick
marks (item times), the A-B loop points and the play state as QML properties, and turns
the bar's gestures into Python callbacks the owner installs:

- ``on_scrub(t, final)``: the cursor was dragged to ``t``; ``final`` is the release
  (the owner emits on its time port while dragging and on ``final``);
- ``on_hover(t | None)``: the pointer is over ``t`` (None: left the bar); the owner may
  answer with ``set_hover_tip(clip, timecode)``, the tip's two readings;
- ``on_step(delta)``: keyboard frame step;
- ``on_toggle_play()``, ``on_loop(a, b)``: transport.

Owner: the video view (the ``timeline`` view type and the shell bar retired with M2).
No channel or document knowledge lives here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot


class TimelineController(QObject):
    rangeChanged = Signal()
    tChanged = Signal()
    hoverChanged = Signal()
    ticksChanged = Signal()
    loopChanged = Signal()
    playingChanged = Signal()
    labelChanged = Signal()
    scrubbingChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._t_min = 0.0
        self._t_max = 1.0
        self._t = 0.0
        self._hover: float | None = None
        self._hover_tip = ("", "")
        self._ticks: list[float] = []
        self._loop_a = -1.0
        self._loop_b = -1.0
        self._playing = False
        self._scrubbing = False
        self._timeline = "index"
        self._frame = -1
        self._frame_count = 0
        self._label = ""
        self.on_scrub: Callable[[float, bool], None] | None = None
        self.on_hover: Callable[[float | None], None] | None = None
        self.on_step: Callable[[int], None] | None = None
        self.on_toggle_play: Callable[[], None] | None = None
        self.on_loop: Callable[[float, float], None] | None = None

    # -- range and cursor ------------------------------------------------------------------
    @Property(float, notify=rangeChanged)
    def tMin(self) -> float:
        return self._t_min

    @Property(float, notify=rangeChanged)
    def tMax(self) -> float:
        return self._t_max

    @Property(str, notify=rangeChanged)
    def timeline(self) -> str:
        return self._timeline

    def set_range(self, t_min: float, t_max: float, timeline: str | None = None) -> None:
        if t_max < t_min:
            t_min, t_max = t_max, t_min
        if t_max == t_min:
            t_max = t_min + 1.0
        changed = (t_min, t_max) != (self._t_min, self._t_max)
        if timeline is not None and timeline != self._timeline:
            self._timeline = timeline
            changed = True
        self._t_min, self._t_max = float(t_min), float(t_max)
        if changed:
            self.rangeChanged.emit()

    @Property(float, notify=tChanged)
    def t(self) -> float:
        return self._t

    @Property(int, notify=tChanged)
    def frame(self) -> int:
        return self._frame

    @Property(int, notify=tChanged)
    def frameCount(self) -> int:
        return self._frame_count

    def set_t(self, t: float, frame: int | None = None, frame_count: int | None = None) -> None:
        t = float(t)
        changed = t != self._t
        self._t = t
        if frame is not None and frame != self._frame:
            self._frame = int(frame)
            changed = True
        if frame_count is not None and frame_count != self._frame_count:
            self._frame_count = int(frame_count)
            changed = True
        if changed:
            self.tChanged.emit()

    @Property(bool, notify=scrubbingChanged)
    def scrubbing(self) -> bool:
        return self._scrubbing

    # -- hover -----------------------------------------------------------------------------
    @Property(float, notify=hoverChanged)
    def hoverT(self) -> float:
        return self._hover if self._hover is not None else -1.0

    @Property(bool, notify=hoverChanged)
    def hovering(self) -> bool:
        return self._hover is not None

    def set_hover(self, t: float | None) -> None:
        if t != self._hover:
            self._hover = None if t is None else float(t)
            self.hoverChanged.emit()

    @Property(str, notify=hoverChanged)
    def hoverClip(self) -> str:
        return self._hover_tip[0]

    @Property(str, notify=hoverChanged)
    def hoverTimecode(self) -> str:
        return self._hover_tip[1]

    def set_hover_tip(self, clip: str, timecode: str) -> None:
        """The hover tip's two readings (the owner's, from ``on_hover``)."""
        if (clip, timecode) != self._hover_tip:
            self._hover_tip = (clip, timecode)
            self.hoverChanged.emit()

    # -- ticks, loop, play -----------------------------------------------------------------
    @Property("QVariantList", notify=ticksChanged)
    def ticks(self) -> list[float]:
        return list(self._ticks)

    def set_ticks(self, ticks: list[float]) -> None:
        ticks = [float(x) for x in ticks]
        if ticks != self._ticks:
            self._ticks = ticks
            self.ticksChanged.emit()

    @Property(float, notify=loopChanged)
    def loopA(self) -> float:
        return self._loop_a

    @Property(float, notify=loopChanged)
    def loopB(self) -> float:
        return self._loop_b

    def set_loop(self, a: float, b: float) -> None:
        a, b = float(a), float(b)
        if (a, b) != (self._loop_a, self._loop_b):
            self._loop_a, self._loop_b = a, b
            self.loopChanged.emit()

    @Property(bool, notify=playingChanged)
    def playing(self) -> bool:
        return self._playing

    def set_playing(self, on: bool) -> None:
        if bool(on) != self._playing:
            self._playing = bool(on)
            self.playingChanged.emit()

    @Property(str, notify=labelChanged)
    def label(self) -> str:
        return self._label

    def set_label(self, text: str) -> None:
        if text != self._label:
            self._label = text
            self.labelChanged.emit()

    # -- gestures from QML -----------------------------------------------------------------
    def clamp(self, t: float) -> float:
        return max(self._t_min, min(self._t_max, float(t)))

    @Slot(float)
    def scrubStart(self, t: float) -> None:
        if not self._scrubbing:
            self._scrubbing = True
            self.scrubbingChanged.emit()
        self.scrubTo(t)

    @Slot(float)
    def scrubTo(self, t: float) -> None:
        t = self.clamp(t)
        self.set_t(t)
        if self.on_scrub:
            self.on_scrub(t, False)

    @Slot(float)
    def scrubEnd(self, t: float) -> None:
        t = self.clamp(t)
        self.set_t(t)
        if self._scrubbing:
            self._scrubbing = False
            self.scrubbingChanged.emit()
        if self.on_scrub:
            self.on_scrub(t, True)

    @Slot(float)
    def hover(self, t: float) -> None:
        t = self.clamp(t)
        self.set_hover(t)
        if self.on_hover:
            self.on_hover(t)

    @Slot()
    def hoverLeave(self) -> None:
        self.set_hover(None)
        self.set_hover_tip("", "")
        if self.on_hover:
            self.on_hover(None)

    @Slot(int)
    def step(self, delta: int) -> None:
        if self.on_step:
            self.on_step(int(delta))

    @Slot()
    def togglePlay(self) -> None:
        if self.on_toggle_play:
            self.on_toggle_play()

    @Slot(float, float)
    def setLoop(self, a: float, b: float) -> None:
        if self.on_loop:
            self.on_loop(float(a), float(b))
        else:
            self.set_loop(a, b)

    def snapshot(self) -> dict[str, Any]:
        return {
            "t": self._t,
            "timeline": self._timeline,
            "range": [self._t_min, self._t_max],
            "frame": self._frame,
            "frame_count": self._frame_count,
            "ticks": len(self._ticks),
            "loop": [self._loop_a, self._loop_b],
            "playing": self._playing,
            "hover": self._hover,
        }


__all__ = ["TimelineController"]
