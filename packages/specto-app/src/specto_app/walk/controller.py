"""``WalkController``: the walk-through (#174) as QML sees it, the root context's
``walkthrough``. It plays a script (``specto_core.walkthrough``) through ``WalkPlayback``
and owns the auto-advance timer; the walk UI binds to its properties and calls its slots.

Entry is from the prompt bar only: the agent's ``walkthrough`` tool calls ``prepare``
(the plan shows while the captions are written), ``walkthrough_play`` calls
``play_script``;
``start(view)`` plays template captions at once, for when there is no agent. The walk
reads resolved link results only and never changes the canvas document.

The walk speed (#183: slow, normal, fast; ``playback.WALK_SPEEDS``) scales every
step's reading time. It is the user's app setting (``QSettings``, like the view
settings), not a canvas param: the walk is not in the document and how fast someone
reads is theirs, the same in every project and every walk. The control bar's speed
button cycles it; the agent sets it with walkthrough_play's ``speed`` when the user
asks for a slower or faster walk, and that sticks for the next one too.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import Property, QObject, QSettings, Qt, QTimer, Signal, Slot

from specto_core.walkthrough import WalkError, caption_script, walk_script

from .playback import NORMAL, PLAYING, PREPARING, WALK_SPEEDS, WalkPlayback

Source = Callable[[], tuple[Mapping[str, Any], Any]]
WALK_SPEED = "walk/speed"  # the walk speed, in the user's settings ini


class WalkController(QObject):
    stateChanged = Signal()  # state, active, autoAdvance
    stepChanged = Signal()  # step, hop, caption, focusViews, dwellMs
    scriptChanged = Signal()  # script, target, hopCount, pathViews
    errorChanged = Signal()
    walkSpeedChanged = Signal()  # walkSpeed (and so dwellMs)
    changed = Signal()  # after any of the above
    #: a step became current (also on replay, a goTo of the same step and play after
    #: pause): start the camera move and the hop's pulse
    stepEntered = Signal(int)
    exited = Signal()  # put the camera back where the user was
    failed = Signal(str)

    def __init__(self, source: Source, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.source = source
        self.playback = WalkPlayback()
        self._error = ""
        #: scales every dwell (tests and scripted flows run the walk fast)
        self.speed = 1.0
        speed = str(QSettings().value(WALK_SPEED, NORMAL) or NORMAL)
        self.playback.speed = speed if speed in WALK_SPEEDS else NORMAL
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        # precise: a coarse timer may land up to 5% early or late, and on Linux it snaps
        # to a shared wake-up, so a restarted dwell could end when the old one would have
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._advance)

    # -- properties ----------------------------------------------------------------------
    @Property(str, notify=stateChanged)
    def state(self) -> str:
        return self.playback.state

    @Property(bool, notify=stateChanged)
    def active(self) -> bool:
        return self.playback.active

    @Property(bool, notify=stateChanged)
    def autoAdvance(self) -> bool:
        return self.playback.state == PLAYING

    @Property(str, notify=scriptChanged)
    def target(self) -> str:
        return self.playback.target

    @Property(int, notify=stepChanged)
    def step(self) -> int:
        return self.playback.step

    @Property(int, notify=scriptChanged)
    def hopCount(self) -> int:
        return self.playback.hop_count

    @Property(int, notify=stepChanged)
    def dwellMs(self) -> int:
        return self.playback.dwell

    @Property("QVariantMap", notify=scriptChanged)
    def script(self) -> dict[str, Any]:
        return self.playback.script or {}

    @Property("QVariantMap", notify=stepChanged)
    def hop(self) -> dict[str, Any]:
        return self.playback.hop

    @Property(str, notify=stepChanged)
    def caption(self) -> str:
        return self.playback.caption

    @Property(list, notify=scriptChanged)
    def pathViews(self) -> list[str]:
        return self.playback.path_views

    @Property(list, notify=stepChanged)
    def focusViews(self) -> list[str]:
        return self.playback.focus_views

    @Property(str, notify=errorChanged)
    def error(self) -> str:
        return self._error

    @Property(str, notify=walkSpeedChanged)
    def walkSpeed(self) -> str:
        return self.playback.speed

    # -- the agent's side (``WalkPlayer``) -----------------------------------------------
    def prepare(self, script: dict[str, Any]) -> None:
        self._change(lambda: self.playback.prepare(script), entered=False)

    def play_script(self, script: dict[str, Any]) -> None:
        if self.playback.cancelled and self.playback.target == script["target"]:
            raise WalkError(
                "the user closed the walk-through while it was being prepared",
                fix="do not play it again unless the user asks",
            )
        self._change(lambda: self.playback.play_script(script), entered=True)

    def set_speed(self, speed: str) -> None:
        """The agent's walkthrough_play ``speed``."""
        if speed not in WALK_SPEEDS:
            raise WalkError(f"no walk speed {speed!r}", fix="use slow, normal or fast")
        self.setWalkSpeed(speed)

    def turn_ended(self) -> None:
        """The prompt turn ended without playing the walk it prepared: play the
        template captions."""
        script = self.playback.script
        if self.playback.state != PREPARING or script is None:
            return
        n = len(script["hops"])
        templates = caption_script(script, [""] * n)
        templates["source"] = "template"
        self._change(lambda: self.playback.play_script(templates), entered=True)

    # -- QML -----------------------------------------------------------------------------
    @Slot(str, result=bool)
    def start(self, view: str) -> bool:
        """Plan the walk to ``view`` and play it with template captions."""
        try:
            data, links = self.source()
            script = walk_script(data, view, links)
        except WalkError as e:
            self._set_error(e.message + (f" ({e.fix})" if e.fix else ""))
            self.failed.emit(self._error)
            return False
        templates = caption_script(script, [""] * len(script["hops"]))
        templates["source"] = "template"
        self._change(lambda: self.playback.play_script(templates), entered=True)
        return True

    @Slot()
    def stepForward(self) -> None:
        self._change(self.playback.forward, entered=True)

    @Slot()
    def stepBack(self) -> None:
        self._change(self.playback.back, entered=True)

    @Slot(int)
    def goTo(self, step: int) -> None:
        self._change(lambda: self.playback.go_to(step), entered=True, always=True)

    @Slot()
    def home(self) -> None:
        self.goTo(0)

    @Slot()
    def end(self) -> None:
        self.goTo(self.playback.last)

    @Slot()
    def play(self) -> None:
        """Play / the play button: auto-advance on from the current step (its pulse
        restarts), or a replay on the summary."""
        self._change(self.playback.play, entered=True, always=True)

    @Slot()
    def pause(self) -> None:
        self._change(self.playback.pause, entered=False)

    @Slot()
    def togglePlay(self) -> None:
        if self.playback.state == PLAYING:
            self.pause()
        else:
            self.play()

    @Slot()
    def replay(self) -> None:
        self._change(self.playback.replay, entered=True, always=True)

    @Slot(str)
    def setWalkSpeed(self, speed: str) -> None:
        """Set the walk speed and keep it in the user's settings; a playing step's dwell
        starts over at the new speed. Not a speed: nothing changes."""
        if speed not in WALK_SPEEDS or speed == self.playback.speed:
            return
        self.playback.speed = speed
        settings = QSettings()
        settings.setValue(WALK_SPEED, speed)
        settings.sync()
        self.walkSpeedChanged.emit()
        self.stepChanged.emit()  # dwellMs
        self.changed.emit()
        self._arm(restart=True)

    @Slot()
    def cycleWalkSpeed(self) -> None:
        """The control bar's speed button: slow, normal, fast, slow again."""
        order = list(WALK_SPEEDS)
        self.setWalkSpeed(order[(order.index(self.playback.speed) + 1) % len(order)])

    @Slot(int)
    def settled(self, step: int) -> None:
        """The UI's camera landed on ``step``: while it plays, its dwell starts over from
        here, so a long camera move does not eat into the time to read the caption. A
        late call for a step no longer current does nothing."""
        if self.playback.state == PLAYING and step == self.playback.step:
            self._arm(restart=True)

    @Slot()
    def exit(self) -> None:
        if self._change(self.playback.exit, entered=False):
            self.exited.emit()

    def release(self) -> None:
        """The app closes: stop the timer and drop the closure over the app, so a closed
        app is freed by reference count (the app's ``_release``)."""
        self.timer.stop()
        self.source = lambda: ({}, None)

    # -- inside ----------------------------------------------------------------------------
    def _set_error(self, text: str) -> None:
        if text != self._error:
            self._error = text
            self.errorChanged.emit()
            self.changed.emit()

    @Slot()
    def _advance(self) -> None:
        if self.playback.state == PLAYING:
            self.stepForward()

    def _change(self, op: Callable[[], Any], *, entered: bool, always: bool = False) -> bool:
        """Run one playback change, then tell QML what moved and arm the timer."""
        pb = self.playback
        before = (pb.state, pb.step, id(pb.script))
        result = op()
        if result is False:
            return False
        after = (pb.state, pb.step, id(pb.script))
        if after[2] != before[2]:
            self._set_error("")
            self.scriptChanged.emit()
        if after[0] != before[0]:
            self.stateChanged.emit()
        if after[1] != before[1] or after[2] != before[2] or after[0] != before[0]:
            self.stepChanged.emit()
        if after != before or always:
            self.changed.emit()
        stepped = after[1] != before[1] or after[2] != before[2] or always
        if entered and stepped and pb.step >= 0:
            self.stepEntered.emit(pb.step)
        self._arm(restart=stepped or after[0] != before[0])
        return True

    def _arm(self, *, restart: bool) -> None:
        if self.playback.state != PLAYING:
            self.timer.stop()
        elif restart or not self.timer.isActive():
            self.timer.start(max(1, int(self.playback.dwell * self.speed)))


__all__ = ["WALK_SPEED", "WalkController"]
