"""The walk-through on stage (#174 part b): what the walk UI needs from the walk
controller (``walkthrough``), shaped
for QML, and the camera that plays it.

The controller owns the script and the playback state (steps, play / pause, the dwell);
this object follows it. On every ``stepEntered`` it moves the camera: the establishing
shot and the summary in the overworld (a combined pan + zoom from the normal view, the
path fitted above the caption band), hops at 1:1 in the normal view (a pan, held when the
hop already fits; out of the overworld a zoom move down to 1:1 first). When the camera
settles the current hop's rings fade in and its pulse runs. Leaving (``exited``) puts the
camera back where the user was, in one move, and restores the maximised view, the
wiring layer and the overworld as they were. The walk never changes the document.

Keys while a walk shows (an event filter on the window, ahead of the canvas's): Left /
Right step, Space plays or pauses, Home and End go to the establishing shot and the
summary, Esc exits (or cancels while preparing); Tab cannot leave for the overworld.
A text field with the focus keeps its keys."""

from __future__ import annotations

import contextlib
import html
import math
import re
import sys
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtCore import Qt as _Qt

from specto_core.float_layer import is_floating
from specto_core.ports import PROJECT_ID

from ..theme import METRICS
from .chains import PROJECT_NODE
from .controller import _editing_text
from .identicon import identicon_url
from .walk_camera import (
    EXIT_MS,
    FADE_MS,
    SAFE_BOTTOM,
    ZOOM_MS,
    ease,
    fit_over,
    hop_fits,
    hop_pan,
    over_of_pan,
    pan_ms,
    union,
    zoom_between,
)

PAD = float(METRICS["pad"])
COLLAPSE_AFTER = 9  # a hop track longer than this folds its middle into "..."
_BOLD = re.compile(r"\*\*(.+?)\*\*")
Key = _Qt.Key
WALK_KEYS = {Key.Key_Left, Key.Key_Right, Key.Key_Space, Key.Key_Home, Key.Key_End}


#: a zero-width space after a path separator: a long path wraps there (#183)
_BREAK = "\u200b"


def breakable(text: str) -> str:
    """``text`` with a line-break opportunity after every '/' and '\\' (a long path in
    body text wraps at its separators rather than mid-name)."""
    return (text or "").replace("/", "/" + _BREAK).replace("\\", "\\" + _BREAK)


def caption_html(text: str) -> str:
    """A caption as the card's rich text: escaped, ``**bold**`` as Semi Bold, paths
    breakable at their separators."""
    return _BOLD.sub(r"<b>\1</b>", breakable(html.escape(text or "", quote=False)))


def count_text(count: Any) -> tuple[str, str]:
    """A flow meter side ``{n, unit}`` or ``{text}`` as (value, unit)."""
    if not isinstance(count, dict):
        return ("", "")
    if count.get("text"):
        return (str(count["text"]), str(count.get("unit", "")))
    n = count.get("n")
    value = f"{n:,}" if isinstance(n, int) else ("" if n is None else str(n))
    return (value, str(count.get("unit", "")))


def hop_track(hops: list[dict[str, Any]], step: int, preparing: bool) -> list[dict[str, Any]]:
    """The hop track's entries in walk order (design: controls, hop track): the project's
    stop, then per hop a connector (or, where a branch into a fork starts, ``branch``:
    the arrow and the identicon of the view it starts from) and the stop of the view it
    reaches unless that view already has one. Each carries the step a click goes to and
    its state: ``past`` (filled), ``current`` (the current hop's ends, ringed) or
    ``future`` (hollow); all hollow on the establishing shot and while preparing, all
    filled on the summary. Longer than nine hops, the middle folds into ``more``."""
    n = len(hops)
    cur = hops[step - 1] if 1 <= step <= n and not preparing else None

    def state_of(hop_step: int) -> str:
        if preparing or step <= 0:
            return "future"
        if hop_step < step:
            return "past"
        return "current" if hop_step == step else "future"

    out: list[dict[str, Any]] = []
    on_track = {PROJECT_ID}
    root_state = "future" if preparing or step <= 0 else "past"
    if cur is not None and cur.get("from") == PROJECT_ID and not cur.get("branch_start"):
        root_state = "current"
    out.append({"kind": "stop", "view": PROJECT_ID, "step": 0, "state": root_state, "hop": 0})
    for i, hop in enumerate(hops, start=1):
        st = state_of(i)
        if hop.get("branch_start"):
            out.append(
                {"kind": "branch", "view": hop.get("from", ""), "step": i, "state": st, "hop": i}
            )
        else:
            out.append(
                {
                    "kind": "link",
                    "view": "",
                    "step": i,
                    "state": "past" if st == "current" else st,
                    "hop": i,
                }
            )
        to = hop.get("to", "")
        if to not in on_track:
            on_track.add(to)
            out.append({"kind": "stop", "view": to, "step": i, "state": st, "hop": i})
        if cur is not None and st == "current":
            # the current hop's from-stop is ringed too (unless a branch marker stands for it)
            for e in out:
                if e["kind"] == "stop" and e["view"] == to:
                    e["state"] = "current"
            if not hop.get("branch_start"):
                for e in reversed(out[:-1]):
                    if e["kind"] == "stop" and e["view"] == hop.get("from"):
                        e["state"] = "current"
                        break
    if n > COLLAPSE_AFTER:
        keep = {1, 2, 3, n - 2, n - 1, n, step - 1, step, step + 1}
        folded: list[dict[str, Any]] = []
        for e in out:
            if e["hop"] == 0 or e["hop"] in keep:
                folded.append(e)
            elif not folded or folded[-1]["kind"] != "more":
                folded.append(
                    {
                        "kind": "more",
                        "view": "",
                        "step": e["step"],
                        "state": e["state"],
                        "hop": e["hop"],
                    }
                )
        out = folded
    return out


def os_reduced_motion() -> bool:
    """The OS asks for less motion (Windows: client-area animations off); the walk then
    cuts its camera moves and lays static tokens along the path."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        on = ctypes.c_bool(True)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)
        return bool(ok) and not on.value
    except Exception:
        return False


class WalkStage(QObject):
    """QML's ``walkStage``: the walk controller's state, shaped for the overlay, and
    the camera that follows its steps."""

    changed = Signal()  # the walk's state, step or script
    cameraChanged = Signal()  # the overworld shot the stage holds (over, zoom, pan)
    settledChanged = Signal()
    #: the camera landed on step ``step``: rings fade in, the pulse starts
    settledOn = Signal(int)
    #: reduced motion: the camera cut; QML cross-fades
    cut = Signal()

    def __init__(
        self,
        controller: Any,
        overworld: Any,
        *,
        motion: bool = True,
        time_scale: float = 1.0,
        panel: Any = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.overworld = overworld
        self.walk: Any = None
        #: the agent panel (``promptBar.panel``): closed while a walk plays
        self.panel = panel
        #: None: not closed for this walk yet; else whether it was pinned open
        self._panel_was: bool | None = None
        self._motion = motion
        self.time_scale = time_scale
        self._active = False
        self._over = False
        self._ow = (1.0, PAD, PAD)
        self._settled = False
        self._settled_step = -1
        self._band = SAFE_BOTTOM
        self._stored: dict[str, Any] | None = None
        self._dwell_epoch = 0
        self._after: Any = None
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.Linear)  # eased here (ease())
        self._anim.valueChanged.connect(self._on_frame)
        self._anim.finished.connect(self._on_landed)
        self._frame: Any = None

    def close(self) -> None:
        """The app closes: no move runs on, nothing follows the walk any more."""
        self._anim.stop()
        self._frame = self._after = None
        self.panel = None
        if self.walk is not None:
            for sig, slot in (
                (self.walk.changed, self._on_changed),
                (self.walk.stepEntered, self._on_step),
                (self.walk.exited, self._on_exited),
            ):
                with contextlib.suppress(RuntimeError, TypeError):
                    sig.disconnect(slot)
            self.walk = None

    # -- the controller ------------------------------------------------------------------
    def attach(self, walk: Any) -> None:
        """Follow ``walk`` (the walk controller, ``walkthrough`` in QML)."""
        self.walk = walk
        walk.changed.connect(self._on_changed)
        walk.stepEntered.connect(self._on_step)
        walk.exited.connect(self._on_exited)
        self._on_changed()

    def _w(self, name: str, default: Any) -> Any:
        if self.walk is None:
            return default
        value = getattr(self.walk, name, default)
        return value() if callable(value) else value

    @Slot()
    def _on_changed(self) -> None:
        active = bool(self._w("active", False))
        if active and not self._active:
            self._begin()
        self._active = active
        if active and self._panel_was is None and self.state != "preparing":
            self._close_panel()
        self.changed.emit()

    def _close_panel(self) -> None:
        """The walk plays: the agent panel it was asked in closes, pinned or not, so it
        covers none of the hop (its pin stays as it was, for after the walk)."""
        panel = self.panel
        if panel is None:
            self._panel_was = False
            return
        self._panel_was = bool(panel.open) and bool(panel.pinned)
        if panel.open:
            panel.escape()

    def _restore_panel(self) -> None:
        """Leaving: a panel that was pinned open opens again."""
        was, self._panel_was = self._panel_was, None
        if was and self.panel is not None and not self.panel.open:
            self.panel.summon()

    def _begin(self) -> None:
        """A walk starts: remember the camera and the view state; no view is maximised
        and the wiring layer goes while it plays."""
        c = self.controller
        self._stored = {
            "pan": (c.pan["x"], c.pan["y"]),
            "overworld": bool(self.overworld.active),
            "maximized": c.maximized,
            "wiring": c.wiring,
        }
        c.maximize("")
        c.setWiring(False)
        self._over = bool(self.overworld.active)
        if self._over:
            self._ow = fit_over(self._bounds(None), self._area())
            self.cameraChanged.emit()
        self._settled = False
        self._settled_step = -1
        self.settledChanged.emit()

    # -- camera ----------------------------------------------------------------------------
    def _area(self) -> tuple[float, float]:
        """The canvas area, less what the caption card takes beyond the 190 px band
        (a long caption grows the card upwards)."""
        v = self.controller.viewport
        w, h = float(v["w"]) or 1280.0, float(v["h"]) or 742.0
        return (w, max(1.0, h - max(0.0, self._band - SAFE_BOTTOM)))

    @Slot(float)
    def setBand(self, px: float) -> None:
        """The caption card and the control bar take ``px`` at the area's bottom (the
        card's top to the area's bottom, plus a gap): camera moves keep out of it. The card
        grows with the caption after the step began, so a settled overworld shot refits."""
        if not math.isfinite(px) or abs(float(px) - self._band) < 2:
            return
        self._band = float(px)
        if self._settled and self._over and self._anim.state() != QAbstractAnimation.State.Running:
            self._refit(None)

    def _over_shot(self) -> tuple[float, float, float]:
        """The establishing shot's and the summary's: the whole path above the card."""
        return fit_over(self._bounds(list(self._w("pathViews", []))), self._area())

    def _rect(self, view_id: str) -> tuple[float, float, float, float] | None:
        if view_id == PROJECT_ID:
            return PROJECT_NODE
        view = self.controller.data["views"].get(view_id)
        if view is None or is_floating(view):
            return None  # a floating view is always on screen: the camera ignores it
        return self.controller.rect_of(view_id)

    def _bounds(self, views: list[str] | None) -> tuple[float, float, float, float]:
        ids = views if views else [PROJECT_ID, *self.controller.data["views"]]
        rects = [r for r in (self._rect(v) for v in ids) if r is not None]
        return union(rects or [PROJECT_NODE])

    def _ms(self, ms: float) -> int:
        return int(ms * self.time_scale) if self._motion else 0

    def _move(self, frame: Any, ms: int, after: Any = None) -> None:
        """Run ``frame(eased t)`` over ``ms``, then ``after()``; no motion: one cut."""
        self._anim.stop()
        self._frame, self._after = frame, after
        if ms <= 0:
            frame(1.0)
            if not self._motion:
                self.cut.emit()
            self._on_landed()
            return
        self._anim.setDuration(ms)
        self._anim.start()

    @Slot("QVariant")
    def _on_frame(self, t: Any) -> None:
        if self._frame is not None:
            self._frame(ease(float(t)))

    @Slot()
    def _on_landed(self) -> None:
        after, self._after, self._frame = self._after, None, None
        if after is not None:
            after()

    def _set_over(self, shot: tuple[float, float, float]) -> None:
        self._ow = shot
        self.cameraChanged.emit()

    def _zoom_to(self, shot: tuple[float, float, float], after: Any, ms: float = ZOOM_MS) -> None:
        start = self._ow
        area = self._area()
        self._move(
            lambda t: self._set_over(zoom_between(start, shot, t, area)), self._ms(ms), after
        )

    def _refit(self, after: Any) -> bool:
        """The card grew under an overworld shot: ease to the new fit (FADE_MS)."""
        shot = self._over_shot()
        if max(abs(a - b) for a, b in zip(shot, self._ow, strict=True)) <= 1:
            return False
        self._zoom_to(shot, after, FADE_MS)
        return True

    def _pan_to(self, pan: tuple[float, float], ms: int | None, after: Any) -> None:
        c = self.controller
        start = (c.pan["x"], c.pan["y"])
        dist = abs(pan[0] - start[0]) + abs(pan[1] - start[1])
        if dist < 0.5:
            self._move(lambda t: None, 0, after)
            return
        dur = self._ms(pan_ms(dist) if ms is None else ms)

        def frame(t: float) -> None:
            c.setPan(start[0] + (pan[0] - start[0]) * t, start[1] + (pan[1] - start[1]) * t)

        self._move(frame, dur, after)

    def _enter_over(self) -> None:
        """Into the overworld at exactly what the normal view shows (zoom 1 at the pan),
        so the zoom move out of it starts where the user is."""
        if self._over:
            return
        c = self.controller
        self._over = True
        self._ow = over_of_pan((c.pan["x"], c.pan["y"]), PAD)
        self.cameraChanged.emit()
        self.overworld.show()

    def _leave_over(self, pan: tuple[float, float], after: Any) -> None:
        """Zoom down to 1:1 at ``pan``, then hand over to the live views there."""

        def land() -> None:
            self.controller.setPan(*pan)
            self.overworld.hide()  # first: the overworld must not refit as it goes
            self._over = False
            self.cameraChanged.emit()
            after()

        self._zoom_to(over_of_pan(pan, PAD), land)

    @Slot(int)
    def _on_step(self, step: int) -> None:
        self._set_settled(False)
        n = int(self._w("hopCount", 0))
        land = lambda: self._land(step)  # noqa: E731
        if step <= 0 or step > n:
            # the establishing shot and the summary: the whole path in the overworld
            self._enter_over()
            self._zoom_to(self._over_shot(), land)
            return
        hop = (self._w("script", {}) or {}).get("hops", [])[step - 1]
        ends = [self._rect(hop.get("from", "")), self._rect(hop.get("to", ""))]
        rects = [r for r in ends if r]
        c = self.controller
        pan = (c.pan["x"], c.pan["y"])
        # out of the overworld the hop never "already fits": frame it
        start = (1e9, 1e9) if self._over else pan
        if rects:
            pan = hop_pan(start, self._area(), PAD, rects)
        go = (
            (lambda p, after: self._leave_over(p, after))
            if self._over
            else (lambda p, after: self._pan_to(p, None, after))
        )
        if len(rects) == 2 and not hop_fits(self._area(), rects):
            # a long link: the camera follows it, from its source to its target (each
            # move capped at 900 ms); the next hop pans back from there
            source = hop_pan(start, self._area(), PAD, [ends[0]])
            go(source, lambda: self._pan_to(pan, None, land))
            return
        go(pan, land)

    def _land(self, step: int, refit: bool = True) -> None:
        # the card took its new caption while the camera moved: an overworld shot
        # computed against the old one refits once
        if refit and self._over and self._refit(lambda: self._land(step, False)):
            return
        self._settled_step = step
        self._set_settled(True)
        self.settledOn.emit(step)
        self._call("settled", step)  # the dwell runs from the landing

    def _set_settled(self, flag: bool) -> None:
        if flag != self._settled:
            self._settled = flag
            self.settledChanged.emit()

    @Slot()
    def _on_exited(self) -> None:
        """Back to where the user was: one move, then the maximised view, the wiring
        layer and the overworld as they were."""
        stored, self._stored = self._stored, None
        self._active = False
        self._set_settled(False)
        self._restore_panel()
        self.changed.emit()
        if stored is None:
            return
        c = self.controller

        def restore() -> None:
            if stored["maximized"]:
                c.maximize(stored["maximized"])
            c.setWiring(stored["wiring"])

        if stored["overworld"]:
            # the user was in the overworld: stay there, released to its own fit
            self._anim.stop()
            self._over = False
            self.cameraChanged.emit()
            if not self.overworld.active:
                self.overworld.show()
            c.setPan(*stored["pan"])
            restore()
            return
        if self._over:
            self._leave_over(stored["pan"], restore)
        else:
            self._pan_to(stored["pan"], EXIT_MS, restore)

    # -- QML: the walk's state -------------------------------------------------------------
    @Property(bool, notify=changed)
    def active(self) -> bool:
        return self._active

    @Property(str, notify=changed)
    def state(self) -> str:
        return str(self._w("state", "idle"))

    @Property(bool, notify=changed)
    def preparing(self) -> bool:
        return self.state == "preparing"

    @Property(bool, notify=changed)
    def playing(self) -> bool:
        return self.state == "playing"

    @Property(int, notify=changed)
    def step(self) -> int:
        return int(self._w("step", -1))

    @Property(int, notify=changed)
    def hopCount(self) -> int:
        return int(self._w("hopCount", 0))

    @Property(bool, notify=changed)
    def atSummary(self) -> bool:
        return self.state == "summary" or (self.hopCount > 0 and self.step == self.hopCount + 1)

    @Property(str, notify=changed)
    def target(self) -> str:
        return str(self._w("target", ""))

    @Property(int, notify=changed)
    def dwellMs(self) -> int:
        """The current step's dwell as the walk's timer runs it (its ``speed`` scales it)."""
        return int(self._w("dwellMs", 0) * float(getattr(self.walk, "speed", 1.0) or 1.0))

    @Property(str, notify=changed)
    def walkSpeed(self) -> str:
        """The user's walk speed (#183): slow, normal or fast."""
        return str(self._w("walkSpeed", "normal"))

    @Property("QVariantList", notify=changed)
    def pathViews(self) -> list[str]:
        return list(self._w("pathViews", []))

    @Property("QVariantList", notify=changed)
    def focusViews(self) -> list[str]:
        return list(self._w("focusViews", []))

    @Property("QVariantMap", notify=changed)
    def script(self) -> dict[str, Any]:
        return dict(self._w("script", {}) or {})

    @Property("QVariantList", notify=changed)
    def pathLinks(self) -> list[str]:
        return [h.get("link", "") for h in self.script.get("hops", [])]

    @Property("QVariantList", notify=changed)
    def hops(self) -> list[dict[str, Any]]:
        """The script's hops for the cards: the caption as rich text, the flow meter's
        sides as text, ``ready`` once its caption is written."""
        out = []
        for h in self.script.get("hops", []):
            vin, uin = count_text(h.get("count_in"))
            vout, uout = count_text(h.get("count_out"))
            out.append(
                {
                    **h,
                    "html": caption_html(h.get("caption", "")),
                    "noteText": breakable(str(h.get("note", "") or "")),
                    "inValue": vin,
                    "inUnit": uin,
                    "outValue": vout,
                    "outUnit": uout,
                    # written: the whole script once played, or a hop whose caption
                    # arrived on its own while the walk is prepared
                    "ready": bool(self.script.get("captioned") or h.get("captioned")),
                    "ratio": -1.0 if h.get("ratio") is None else float(h["ratio"]),
                }
            )
        return out

    @Property("QVariantMap", notify=changed)
    def hop(self) -> dict[str, Any]:
        """The current hop as ``hops`` has it; ``{}`` on the establishing shot and the
        summary."""
        hops, step = self.hops, self.step
        return hops[step - 1] if 1 <= step <= len(hops) else {}

    @Property(str, notify=changed)
    def captionHtml(self) -> str:
        return caption_html(str(self._w("caption", "")))

    @Property("QVariantMap", notify=changed)
    def meter(self) -> dict[str, str]:
        """The establishing shot's and the summary's flow meter: what the project holds
        and what reaches the target (the last hop's out side)."""
        s = self.script
        hops = s.get("hops", [])
        vin, uin = count_text((s.get("project") or {}).get("count"))
        vout, uout = count_text(hops[-1].get("count_out")) if hops else ("", "")
        return {"inValue": vin, "inUnit": uin, "outValue": vout, "outUnit": uout}

    @Property("QVariantList", notify=changed)
    def track(self) -> list[dict[str, Any]]:
        return hop_track(self.script.get("hops", []), self.step, self.preparing)

    @Property(str, notify=changed)
    def counter(self) -> str:
        if self.preparing:
            return "preparing"
        if self.atSummary:
            return "summary"
        if self.step <= 0:
            return "start"
        return f"{self.step} / {self.hopCount}"

    @Property("QVariantMap", notify=changed)
    def chips(self) -> dict[str, str]:
        """The summary's chips: view id -> its short words (the project's too)."""
        s = self.script
        out = {PROJECT_ID: str((s.get("project") or {}).get("chip", ""))}
        for vid, v in (s.get("views") or {}).items():
            if v.get("chip"):
                out[vid] = str(v["chip"])
        for h in s.get("hops", []):
            if h.get("chip") and h.get("to") not in out:
                out[h["to"]] = str(h["chip"])
        return {k: v for k, v in out.items() if v}

    @Property("QVariantMap", notify=changed)
    def faces(self) -> dict[str, dict[str, str]]:
        """Every path view's name, label, chain token and identicon (the chain-coloured
        one and the neutral one future stops show)."""
        c = self.controller
        out = {}
        for vid in [PROJECT_ID, *self.pathViews]:
            v = c.views.get(vid, {})
            out[vid] = {
                "name": vid,
                "label": "project" if vid == PROJECT_ID else str(v.get("label", vid)),
                "chain": "" if vid == PROJECT_ID else str(v.get("chain", "")),
                "icon": c.identicon_of(vid),
                "plain": identicon_url(vid if vid != PROJECT_ID else "root", None, round=True),
            }
        return out

    @Property(str, notify=changed)
    def statusLine(self) -> str:
        if not self._active:
            return ""
        tail = "Esc cancel" if self.preparing else "← → step · space play / pause · Esc exit"
        return f"walk-through: {self.target} · {tail}"

    @Property(str, notify=changed)
    def askText(self) -> str:
        hop = self.hop
        if hop:
            return f"about hop {self.step} ({hop.get('from', '')} → {hop.get('to', '')}): "
        return f"about the walk through {self.target}: "

    # -- QML: the camera -------------------------------------------------------------------
    @Property(bool, notify=cameraChanged)
    def over(self) -> bool:
        """The stage holds the overworld's camera (the establishing shot, the summary)."""
        return self._over

    @Property(float, notify=cameraChanged)
    def overZoom(self) -> float:
        return self._ow[0]

    @Property(float, notify=cameraChanged)
    def overX(self) -> float:
        return self._ow[1]

    @Property(float, notify=cameraChanged)
    def overY(self) -> float:
        return self._ow[2]

    @Property(bool, notify=settledChanged)
    def settled(self) -> bool:
        return self._settled

    @Property(int, notify=settledChanged)
    def settledStep(self) -> int:
        return self._settled_step

    @Property(bool, constant=True)
    def reducedMotion(self) -> bool:
        return not self._motion

    # -- QML: the controls ---------------------------------------------------------------
    def _call(self, name: str, *args: Any) -> None:
        fn = getattr(self.walk, name, None) if self.walk is not None else None
        if fn is not None:
            fn(*args)

    @Slot()
    def stepForward(self) -> None:
        self._call("stepForward")

    @Slot()
    def stepBack(self) -> None:
        self._call("stepBack")

    @Slot(int)
    def goTo(self, step: int) -> None:
        self._call("goTo", int(step))

    @Slot()
    def home(self) -> None:
        self._call("home")

    @Slot()
    def end(self) -> None:
        self._call("end")

    @Slot()
    def togglePlay(self) -> None:
        self._call("togglePlay")

    @Slot()
    def pause(self) -> None:
        """Any canvas input while a walk plays (a click, a pan, the wheel) pauses it."""
        if self.playing:
            self._call("pause")

    @Slot()
    def replay(self) -> None:
        self._call("replay")

    @Slot()
    def cycleWalkSpeed(self) -> None:
        self._call("cycleWalkSpeed")

    @Slot()
    def exit(self) -> None:
        self._call("exit")

    @Slot(str)
    def frameView(self, view_id: str) -> None:
        """A name on the caption card: the camera frames that view; the walk pauses."""
        self.pause()
        rect = self._rect(view_id)
        if rect is None:
            self.controller.flyTo(view_id)
            return
        c = self.controller
        pan = hop_pan((c.pan["x"], c.pan["y"]), self._area(), PAD, [rect])
        if self._over:
            self._leave_over(pan, lambda: c.flyTo(view_id))
        else:
            self._pan_to(pan, None, lambda: c.flyTo(view_id))

    # -- keys ------------------------------------------------------------------------------
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Installed on the window after the canvas controller's, so it runs first."""
        if not self._active:
            return False
        t = event.type()
        if t not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        key = event.key()
        if key == Key.Key_Escape or key == Key.Key_Tab:
            if _editing_text(obj) and key == Key.Key_Escape:
                return False
        elif key not in WALK_KEYS or _editing_text(obj):
            return False
        if t == QEvent.Type.ShortcutOverride:
            event.accept()  # the window's shortcuts (Esc, Tab) stay out of it
            return True
        if t == QEvent.Type.KeyPress:
            {
                Key.Key_Left: self.stepBack,
                Key.Key_Right: self.stepForward,
                Key.Key_Space: self.togglePlay,
                Key.Key_Home: self.home,
                Key.Key_End: self.end,
                Key.Key_Escape: self.exit,
                Key.Key_Tab: lambda: None,
            }[key]()
        return True


__all__ = [
    "WalkStage",
    "breakable",
    "caption_html",
    "count_text",
    "hop_track",
    "os_reduced_motion",
]
