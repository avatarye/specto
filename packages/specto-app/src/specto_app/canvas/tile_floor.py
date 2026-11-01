"""QML's ``tileFloor`` (#191, #201): the canvas tile floor's cues (``canvas.floor``), fed
by the app's events, as the uniforms ``TileFloor.qml`` hands its shader.

Event sources: the agent icon's state (thinking and working loop bands, needs-you's
double band and glow) and its finished turns (done, when the turn ends with nothing
waiting), views created on the canvas (bands, or the far chip), the selection (a
footprint per selected view, hidden while a gesture holds it), the wiring layer (the
tiles dim to 30 %) and the OS's reduced-motion setting. Every view cue takes the rect
the controller shows the view at (``shown_rect``, #208): a floater's window rect in
screen space, a collapsed view's title bar; ``viewsChanged`` moves the footprints after
a float, a collapse, a floater's drop or a window resize.

The clock ticks only while a cue changes from frame to frame (``FloorCues.animating``);
a resting floor arms one single-shot wake-up for its next change (a needs-you cycle) or
none at all, and ``uniforms`` only changes when a value does, so an idle floor costs no
frame. The uniforms carry at most eight effects as three vec4 each (the shader has no
arrays): ``g`` geometry, ``p`` (R, w, A), ``c`` colour with ``a`` = world space.
``worldLive``: a world-space cue is on, so the floor takes the canvas camera (and
re-renders as it pans); otherwise a pan costs it nothing.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QVector4D

from .floor import MAX_EFFECTS, Effect, FloorCues, floor_grid

TICK_MS = 16
DIM = 0.3  # the tiles' opacity under the wiring layer


def _empty_uniforms() -> dict[str, Any]:
    zero = QVector4D(0, 0, 0, 0)
    out: dict[str, Any] = {"worldLive": False}
    for i in range(MAX_EFFECTS):
        for key in "gpc":
            out[f"{key}{i}"] = zero
    return out


class TileFloor(QObject):
    uniformsChanged = Signal()
    dimChanged = Signal()
    chipChanged = Signal()
    tickingChanged = Signal()

    def __init__(
        self,
        controller: Any,
        *,
        prompt_bar: Any = None,
        overworld: Any = None,
        theme: Any = None,
        reduced_motion: bool = False,
        clock: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.prompt_bar = prompt_bar
        self.overworld = overworld
        self.theme = theme
        self.clock = clock
        self.cues = FloorCues(reduced_motion=reduced_motion)
        self._uniforms = _empty_uniforms()
        self._chip: dict[str, Any] = {"show": False}
        self._known = {e["id"] for e in controller.viewIds}
        self._colours: dict[str, QColor] = {}
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)
        self._wake = QTimer(self)
        self._wake.setSingleShot(True)
        self._wake.timeout.connect(self.tick)
        self._offs: list[tuple[Any, Any]] = []
        self._on(controller.viewIdsChanged, self._views_changed)
        self._on(controller.selectionChanged, self._selection_changed)
        # a float, a collapse, a floater's drop or a window resize moves a view's shown
        # rect without changing its ids (#208): the footprints follow
        self._on(controller.viewsChanged, self._selection_changed)
        self._on(controller.dragRectsChanged, self._drag_changed)
        self._on(controller.wiringChanged, self.dimChanged)
        if prompt_bar is not None:
            self._on(prompt_bar.icon.changed, self._agent_changed)
            self._on(prompt_bar.turnFinished, self._turn_finished)
        if overworld is not None:
            self._on(overworld.activeChanged, self.dimChanged)
        if theme is not None:
            self._on(theme.changed, self._theme_changed)

    def _on(self, signal: Any, slot: Any) -> None:
        signal.connect(slot)
        self._offs.append((signal, slot))

    def close(self) -> None:
        self._timer.stop()
        self._wake.stop()
        for signal, slot in self._offs:
            with contextlib.suppress(RuntimeError, TypeError):
                signal.disconnect(slot)
        self._offs = []

    # -- QML ------------------------------------------------------------------------------------
    @Slot(float, float, float, result="QVariantMap")
    def grid(self, w: float, h: float, dpr: float) -> dict[str, Any]:
        """The floor's texture and cells for a canvas area of ``w x h`` px on a screen of
        ``dpr`` (#197: fixed to the screen, so only a resize changes it)."""
        g = floor_grid(w, h, dpr)
        return {
            "drawW": g.draw_w,
            "drawH": g.draw_h,
            "texW": g.tex_w,
            "texH": g.tex_h,
            "n": g.n,
            "cols": g.cols,
            "rows": g.rows,
            "marginX": g.margin_x,
            "marginY": g.margin_y,
            "unit": g.unit,
        }

    @Property("QVariantMap", notify=uniformsChanged)
    def uniforms(self) -> dict[str, Any]:
        return self._uniforms

    @Property(float, notify=dimChanged)
    def dim(self) -> float:
        wiring = bool(self.controller.wiring)
        over = bool(self.overworld is not None and self.overworld.active)
        return DIM if wiring and not over else 1.0

    @Property("QVariantMap", notify=chipChanged)
    def chip(self) -> dict[str, Any]:
        return self._chip

    @Property(bool, notify=tickingChanged)
    def ticking(self) -> bool:
        return self._timer.isActive()

    @Property(bool, constant=True)
    def reducedMotion(self) -> bool:
        return self.cues.reduced_motion

    @Slot(float, float)
    def setAgentAnchor(self, x: float, y: float) -> None:
        """The S icon's centre in the canvas area's px (Main.qml reports it)."""
        if (x, y) != self.cues.agent_anchor:
            self.cues.set_agent_anchor(x, y)
            self.tick()

    @Slot()
    def chipClicked(self) -> None:
        view = self.cues.chip_clicked(self.clock())
        self.tick()
        if view:
            self.controller.jumpTo(view)

    # -- events ---------------------------------------------------------------------------------
    @Slot()
    def _agent_changed(self) -> None:
        self.cues.agent(self.prompt_bar.icon.state, self.clock())
        self.tick()

    @Slot()
    def _turn_finished(self) -> None:
        if self.prompt_bar.icon.state == "idle":
            self.cues.turn_done(self.clock())
            self.tick()

    @Slot()
    def _views_changed(self) -> None:
        now = self.clock()
        ids = {e["id"] for e in self.controller.viewIds}
        over = self.overworld is not None and self.overworld.active
        vp = self.controller.viewport_rect()
        for vid in ids - self._known:
            # the rect the view is shown at (#208): a floater's window rect, a
            # collapsed view's title bar
            rect, space = self.controller.shown_rect(vid)
            # the overworld shows the whole canvas: every new view is on screen there
            viewport = rect if over else vp
            self.cues.view_created(vid, rect, self._chain(vid), viewport, now, space)
        self._known = ids
        self._selection_changed()

    def _chain(self, vid: str) -> str:
        return self.controller.chain_of(vid) or "chain_none"

    @Slot()
    def _selection_changed(self) -> None:
        ids = {e["id"] for e in self.controller.viewIds}
        sel = {}
        for vid in self.controller.selectedViews:
            if vid in ids:
                rect, space = self.controller.shown_rect(vid)
                sel[vid] = (rect, self._chain(vid), space)
        self.cues.select(sel, self.clock())
        self.tick()

    @Slot()
    def _drag_changed(self) -> None:
        self.cues.drag(set(self.controller.dragRects), self.clock())
        self.tick()

    @Slot()
    def _theme_changed(self) -> None:
        self._colours = {}
        self._uniforms = {}
        self.tick()

    # -- the clock ------------------------------------------------------------------------------
    def _colour(self, token: str) -> QColor:
        c = self._colours.get(token)
        if c is None:
            name = self.theme.color(token) if self.theme is not None else "#ff00ff"
            c = self._colours[token] = QColor(name)
        return c

    def _pack(self, effects: list[Effect]) -> dict[str, Any]:
        out = _empty_uniforms()
        out["worldLive"] = any(e.space == "world" for e in effects)
        for i, e in enumerate(effects):
            c = self._colour(e.colour)
            out[f"g{i}"] = QVector4D(*e.geom)
            out[f"p{i}"] = QVector4D(e.R, e.w, e.A, 0.0)
            out[f"c{i}"] = QVector4D(c.redF(), c.greenF(), c.blueF(), e.space == "world")
        return out

    @Slot()
    def tick(self) -> None:
        """One frame of the cues: the uniforms, the chip, and the clock's next step."""
        now = self.clock()
        u = self._pack(self.cues.effects(now))
        if u != self._uniforms:
            self._uniforms = u
            self.uniformsChanged.emit()
        chip = self.cues.chip(now)
        c = (
            {
                "show": True,
                "view": chip.view,
                "text": chip.text,
                "x": chip.x,
                "y": chip.y,
                "angle": chip.angle,
                "chain": chip.chain,
            }
            if chip
            else {"show": False}
        )
        if c != self._chip:
            self._chip = c
            self.chipChanged.emit()
        was = self._timer.isActive()
        if self.cues.animating(now):
            self._wake.stop()
            if not was:
                self._timer.start()
        else:
            self._timer.stop()
            wakes = [t for t in (self.cues.next_change(now), chip and chip.until) if t]
            nxt = min(wakes) if wakes else None
            if nxt is not None:
                self._wake.start(max(1, int((nxt - now) * 1000)))
            else:
                self._wake.stop()
        if was != self._timer.isActive():
            self.tickingChanged.emit()


__all__ = ["DIM", "TICK_MS", "TileFloor"]
