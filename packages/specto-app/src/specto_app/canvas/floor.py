"""The canvas tile floor's maths (#191, #197; v3 #201, design "08c Tile floor v3"): the
screen-fixed grid, and the cues (agent status, new views, the selection footprint) as
the few effects the floor's shader takes. No Qt here: ``tile_floor.TileFloor`` drives
:class:`FloorCues` with the app's events and its clock, and hands
:meth:`FloorCues.effects` to ``TileFloor.qml`` as uniforms.

The floor is fixed to the screen (#197): it neither pans nor zooms with the canvas, and
only a window resize changes it. It renders at full resolution (#201): a cell is
:data:`CELL_TEXELS` (8) texture px, 8 screen px, holding a square tile of 3 at rest (at
offset 2) that grows to 6 (at offset 1, a 2 px gap between lit tiles) as a cue lights it.
A whole number of cells spans the area, the few texels over split on both sides.
Everything is counted in device px: a texture px is ``round(dpr)`` of them
(:func:`texel_px`), so every tile draws the same width at any display scale. Square
tiles, not rounded (``tools/bench_floor_shape.py``, measured for #191).

Cues (the design's motion spec, 08c frame 08): ``e`` at a cell's centre is the max over
the live effects of the triangular band ``A * max(0, 1 - |d - R| / w)`` with ``d`` the
distance to the effect's point, or to its view rectangle (0 inside); the strongest effect
gives the colour, and the tile's side is :func:`tile_side` of ``e``. Agent status runs in
screen space at the S icon; view cues sit on their world rectangles mapped to the screen
through the canvas camera, with R and w in screen px whatever the zoom, so a pulse
follows its view while the tiles stay put. Every view cue takes the rect the view is
SHOWN at (#208): a collapsed view's title bar, and a floating view's window rect, in
screen space like the agent's cues, so a pan leaves it put. The selection is a
footprint: every tile the view covers lights at :data:`SELECT_A` (0.4, #220: a 4 px tile
in a 40 % tint of the chain colour, a cue under the view rather than a lit ring), falling
off over :data:`SELECT_W` px around it; no outline.
Thinking loops a band every :data:`THINK_PERIOD_S` for as long as the agent thinks. A new
view beyond :data:`MAX_PULSE_DISTANCE` gets three pulses at the window edge and the far
chip instead of bands. Reduced motion turns every travelling band into a single 600 ms
bump of the same tiles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

RES = 1  # screen px per texture px: the floor renders at full resolution
CELL_TEXELS = 8  # texture px per cell: 8 screen px
MAX_EFFECTS = 8
#: a new view farther than this from the window (world px) gets the far chip, not bands
MAX_PULSE_DISTANCE = 1110.0
NEAR_REACH = 340.0  # near bands travel this far past the window edge
FAR_OUTSIDE = 28.0  # far pulses start this far outside the window edge
CHIP_S = 4.0
FADE_S = 0.6  # reduced motion: one bump of the same tiles
#: the agent's pulses at the S icon: a thinking band every period (tune 1.2-1.6 s), its
#: life; the needs-attention double band every cycle, 150 ms apart; the done band
THINK_PERIOD_S, THINK_LIFE_S = 1.2, 1.8
NEEDS_PERIOD_S, NEEDS_LIFE_S, NEEDS_GAP_S = 4.0, 1.6, 0.15
DONE_LIFE_S = 1.6
BAND_W = 32.0  # a travelling band's half-width, screen px (the done band's is 48)
SELECT_W = 20.0  # the selection footprint falls off over this many px around the view (#207)
#: the footprint's amplitude (#220): e 0.4 under the view, a 4 px tile at 40 % of the chain
#: colour; the rim's one cell out (d 8) climbs to 4 px too, the next rests
SELECT_A = 0.4
GLOW_W, GLOW_A = 80.0, 0.35  # the held needs-attention glow at the S icon

Rect = tuple[float, float, float, float]  # x, y, w, h


@dataclass(frozen=True)
class FloorGrid:
    """The floor's texture and cells for a canvas area of a given size. ``texel``:
    device px per texture px (dpr, rounded: whole, so nearest sampling draws every
    texel the same width); ``tex_w, tex_h``: the texture, texture px; ``draw_w,
    draw_h``: its size in area px (it starts at the area's top-left); ``n``: texture px
    per cell; ``side``: a resting tile's; ``cols, rows``: the whole cells across and
    down; ``margin_x, margin_y``: texture px before the first cell; ``unit``: area px
    per texture px."""

    texel: int
    tex_w: int
    tex_h: int
    draw_w: float
    draw_h: float
    n: int
    side: int
    cols: int
    rows: int
    margin_x: int
    margin_y: int
    unit: float
    dpr: float


def texel_px(dpr: float) -> int:
    """Device px per texture px: the nearest whole number to RES x dpr."""
    return max(1, math.floor(RES * max(dpr, 0.01) + 0.5))


def floor_grid(width: float, height: float, dpr: float = 1.0) -> FloorGrid:
    """The grid for a canvas area of ``width x height`` px on a screen of ``dpr`` device
    px per px: nothing else moves it."""
    dpr = dpr if dpr > 0 else 1.0
    f = texel_px(dpr)
    wd, hd = max(0.0, width) * dpr, max(0.0, height) * dpr
    inner_w, inner_h = math.floor(wd / f), math.floor(hd / f)  # whole texels inside
    n = CELL_TEXELS
    cols, rows = inner_w // n, inner_h // n
    tw, th = math.ceil(wd / f), math.ceil(hd / f)
    return FloorGrid(
        texel=f,
        tex_w=tw,
        tex_h=th,
        draw_w=tw * f / dpr,
        draw_h=th * f / dpr,
        n=n,
        side=tile_side(n, 0.0),
        cols=cols,
        rows=rows,
        margin_x=(inner_w - cols * n) // 2,
        margin_y=(inner_h - rows * n) // 2,
        unit=f / dpr,
        dpr=dpr,
    )


def tile_side(n: int, e: float) -> int:
    """A tile's side in texture px for a cell of ``n``: the ladder ``floor(3.5 + 3 e)``
    of an 8 px cell, 3 at rest to 6 lit (4 from e 1/6, 5 from 1/2, 6 from 5/6), scaled
    by ``n / 8`` (the shader's ``clamp(floor(n * (3.5 + 3 e) / 8), rest, lit)``)."""
    rest, lit = math.floor(3 * n / 8), math.floor(6 * n / 8)
    return int(min(lit, max(rest, math.floor(n * (3.5 + 3 * e) / 8))))


def ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 2


def ease_in(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t


def band_amplitude(p: float) -> float:
    """A travelling band's amplitude at progress ``p`` (eased): ``1 - p^3``, full for
    most of the way and gone at the reach."""
    return max(0.0, 1.0 - p**3)


def band_profile(d: float, R: float, w: float, A: float) -> float:
    """``e`` of one effect at distance ``d`` (the shader's ``cue``): a triangular band of
    half-width ``w`` about ``R``, ``A`` at its crest."""
    return A * max(0.0, 1.0 - abs(d - R) / w)


# -- effects -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Effect:
    """One live term of the floor: ``geom`` is a point (x, y, x, y) or a rect (x0, y0,
    x1, y1), in world px (``space`` "world") or area px ("screen"); R and w are always
    screen px."""

    group: str  # agent | tie | view: the budget's priority
    space: str
    geom: tuple[float, float, float, float]
    R: float
    w: float
    A: float
    colour: str
    view: str = ""


@dataclass
class _Pulse:
    """A scheduled band: lives ``life`` s from ``start``; ``kind`` picks R(t) and A(t)."""

    kind: str
    start: float
    life: float
    group: str
    space: str
    geom: tuple[float, float, float, float]
    colour: str
    born: float  # the event's time: the budget drops the oldest event first
    view: str = ""
    reach: float = 0.0  # near bands: d + 340


@dataclass
class _Tie:
    rect: Rect
    chain: str
    since: float  # fade-in start
    until: float | None = None  # fade-out start
    dragged: bool = False
    space: str = "world"  # "screen": a floater's rect, the area's px (#208)


#: a view's shown rect for :meth:`FloorCues.select`: (rect, chain) in world px, or
#: (rect, chain, "screen") for a floating view's window rect in area px (#208)
Shown = tuple[Rect, str] | tuple[Rect, str, str]


@dataclass(frozen=True)
class Chip:
    view: str
    text: str
    x: float  # area px: where the chip points from, inside the window
    y: float
    angle: float  # radians, the direction to the view (0 = right)
    until: float
    chain: str = ""


def distance_text(d: float) -> str:
    return f"{d / 1000:.1f}k px" if d >= 1000 else f"{round(d)} px"


def _rect_gap(a: Rect, b: Rect) -> float:
    """Distance between two rects (0 when they touch or overlap)."""
    dx = max(b[0] - (a[0] + a[2]), a[0] - (b[0] + b[2]), 0.0)
    dy = max(b[1] - (a[1] + a[3]), a[1] - (b[1] + b[3]), 0.0)
    return math.hypot(dx, dy)


def _edge_point(vp: Rect, target: tuple[float, float], outside: float) -> tuple[float, float]:
    """Where the line from the viewport's centre to ``target`` leaves the viewport grown
    by ``outside`` px (``outside`` < 0 shrinks it)."""
    cx, cy = vp[0] + vp[2] / 2, vp[1] + vp[3] / 2
    dx, dy = target[0] - cx, target[1] - cy
    hx, hy = vp[2] / 2 + outside, vp[3] / 2 + outside
    t = min(hx / abs(dx) if dx else math.inf, hy / abs(dy) if dy else math.inf)
    return cx + dx * t, cy + dy * t


@dataclass
class FloorCues:
    """The cues on the floor over time. Inputs: :meth:`agent` (the S icon's state),
    :meth:`turn_done`, :meth:`view_created`, :meth:`select`, :meth:`drag`; outputs:
    :meth:`effects` (at most :data:`MAX_EFFECTS`), :meth:`animating` (the clock must
    tick), :meth:`next_change` (a static floor's next wake-up), :meth:`chip`."""

    reduced_motion: bool = False
    agent_anchor: tuple[float, float] = (0.0, 0.0)
    _pulses: list[_Pulse] = field(default_factory=list)
    _ties: dict[str, _Tie] = field(default_factory=dict)
    _busy_since: float | None = None  # thinking or working
    _busy_until: float | None = None  # reduced motion: the held glow fades out
    _needs_since: float | None = None
    _needs_until: float | None = None
    _chip: Chip | None = None

    def set_agent_anchor(self, x: float, y: float) -> None:
        self.agent_anchor = (float(x), float(y))

    # -- inputs ---------------------------------------------------------------------------------
    def agent(self, state: str, now: float) -> None:
        busy = state in ("thinking", "working")
        if busy and self._busy_since is None:
            self._busy_since, self._busy_until = now, None
        elif not busy and self._busy_since is not None:
            self._end_busy(now)
        if state == "needs_you" and self._needs_since is None:
            self._needs_since, self._needs_until = now, None
        elif state != "needs_you" and self._needs_since is not None:
            self._end_needs(now)

    def _end_busy(self, now: float) -> None:
        # the bands already out finish their life
        start = self._busy_since
        assert start is not None
        if not self.reduced_motion:
            for s in self._loop_starts(start, THINK_PERIOD_S, start, now):
                self._add_agent_band("think", s, THINK_LIFE_S, start)
        self._busy_since, self._busy_until = None, now

    def _end_needs(self, now: float) -> None:
        start = self._needs_since
        assert start is not None
        if not self.reduced_motion:
            for c in self._loop_starts(start, NEEDS_PERIOD_S, start, now):
                for s in (c, c + NEEDS_GAP_S):
                    if s <= now:
                        self._add_agent_band("needs", s, NEEDS_LIFE_S, start)
        self._needs_since, self._needs_until = None, now

    def turn_done(self, now: float) -> None:
        if self.reduced_motion:
            self._add_agent_band("done_fade", now, FADE_S, now)
        else:
            self._add_agent_band("done", now, DONE_LIFE_S, now)

    def _add_agent_band(self, kind: str, start: float, life: float, born: float) -> None:
        x, y = self.agent_anchor
        colour = "ok" if kind.startswith("done") else "agent"
        p = _Pulse(kind, start, life, "agent", "screen", (x, y, x, y), colour, born)
        if all((q.kind, q.start) != (kind, start) for q in self._pulses):
            self._pulses.append(p)

    def view_created(
        self, view: str, rect: Rect, chain: str, viewport: Rect, now: float, space: str = "world"
    ) -> str:
        """A new view at ``rect`` (world px) while the window shows ``viewport`` (world
        px, zoom 1): ``"on"`` screen, ``"near"`` or ``"far"``; the bands, or the edge
        pulses and the chip. A view shown in ``space`` "screen" (a floater, #208) gives
        its rect in area px: it is on screen, and its bands stay fixed there."""
        x, y, w, h = rect
        geom = (x, y, x + w, y + h)
        d = 0.0 if space == "screen" else _rect_gap(rect, viewport)
        if d <= 0:
            where = "on"
        elif d <= MAX_PULSE_DISTANCE:
            where = "near"
        else:
            where = "far"
        if where != "far":
            if self.reduced_motion:
                self._pulses.append(
                    _Pulse("view_fade", now, FADE_S, "view", space, geom, chain, now, view)
                )
                return where
            life = 1.2 if where == "on" else min(2.0, 1.2 + d / 500.0)
            kind = "view_on" if where == "on" else "view_near"
            for s in (now, now + 0.25):
                self._pulses.append(
                    _Pulse(kind, s, life, "view", space, geom, chain, now, view, d + NEAR_REACH)
                )
            return where
        # far: pulses at the window edge (screen) and the chip
        target = (x + w / 2, y + h / 2)
        ex, ey = _edge_point(viewport, target, FAR_OUTSIDE)
        sx, sy = ex - viewport[0], ey - viewport[1]  # area px
        point = (sx, sy, sx, sy)
        if self.reduced_motion:
            self._pulses.append(
                _Pulse("far_fade", now, FADE_S, "view", "screen", point, chain, now, view)
            )
        else:
            for s in (now, now + 0.6, now + 1.2):
                self._pulses.append(
                    _Pulse("far", s, 1.0, "view", "screen", point, chain, now, view)
                )
        cx, cy = _edge_point(viewport, target, -48.0)
        vcx, vcy = viewport[0] + viewport[2] / 2, viewport[1] + viewport[3] / 2
        self._chip = Chip(
            view=view,
            text=distance_text(d),
            x=cx - viewport[0],
            y=cy - viewport[1],
            angle=math.atan2(target[1] - vcy, target[0] - vcx),
            until=now + CHIP_S,
            chain=chain,
        )
        return where

    def select(self, views: dict[str, Shown], now: float) -> None:
        """The selection now: view id -> (rect in world px, chain token), or (rect in
        area px, chain, "screen") for a view shown on the float layer (#208). Called
        again with new rects (a float, a collapse, a floater's drop) it moves the
        footprints without restarting them."""
        for vid, (rect, chain, *rest) in views.items():
            space = rest[0] if rest else "world"
            tie = self._ties.get(vid)
            if tie is None or (tie.until is not None and not tie.dragged):
                self._ties[vid] = _Tie(rect, chain, now, space=space)
            else:
                tie.rect, tie.chain, tie.space = rect, chain, space
        for vid, tie in self._ties.items():
            if vid not in views and tie.until is None:
                tie.until = now

    def drag(self, views: set[str], now: float) -> None:
        """The views a gesture holds: their footprints fade out until the drop."""
        for vid, tie in self._ties.items():
            if vid in views and not tie.dragged:
                tie.dragged = True
                if tie.until is None:
                    tie.until = now
            elif vid not in views and tie.dragged:
                tie.dragged = False
                tie.since, tie.until = now, None

    def chip(self, now: float) -> Chip | None:
        if self._chip is not None and now >= self._chip.until:
            self._chip = None
        return self._chip

    def chip_clicked(self, now: float) -> str:
        chip, self._chip = self.chip(now), None
        return chip.view if chip else ""

    # -- outputs --------------------------------------------------------------------------------
    @staticmethod
    def _loop_starts(origin: float, every: float, frm: float, to: float) -> list[float]:
        """The loop's starts (``origin + i * every``) within [frm, to]."""
        if to < frm:
            return []
        i0 = max(0, math.ceil((frm - origin) / every - 1e-9))
        i1 = math.floor((to - origin) / every + 1e-9)
        return [origin + i * every for i in range(i0, i1 + 1)]

    def _live_pulses(self, now: float) -> list[_Pulse]:
        out = [p for p in self._pulses if p.start <= now < p.start + p.life]
        if self.reduced_motion:
            return out
        x, y = self.agent_anchor
        anchor = (x, y, x, y)
        if self._busy_since is not None:
            b = self._busy_since
            for s in self._loop_starts(b, THINK_PERIOD_S, now - THINK_LIFE_S, now):
                if s > now - THINK_LIFE_S:
                    out.append(
                        _Pulse("think", s, THINK_LIFE_S, "agent", "screen", anchor, "agent", b)
                    )
        if self._needs_since is not None:
            n0 = self._needs_since
            for c in self._loop_starts(n0, NEEDS_PERIOD_S, now - NEEDS_LIFE_S - NEEDS_GAP_S, now):
                for s in (c, c + NEEDS_GAP_S):
                    if s <= now < s + NEEDS_LIFE_S:
                        life = NEEDS_LIFE_S
                        out.append(_Pulse("needs", s, life, "agent", "screen", anchor, "agent", n0))
        # a band also kept in _pulses (the loop ended) must not count twice
        seen, uniq = set(), []
        for p in out:
            key = (p.kind, p.start, p.view)
            if key not in seen:
                seen.add(key)
                uniq.append(p)
        return uniq

    def _pulse_effect(self, p: _Pulse, now: float) -> Effect:
        """The motion spec's R(t), w and A(t): every travelling band is
        ``R = R0 + reach * easeOut(t / T)``, ``A = 1 - p^3``."""
        t = now - p.start
        u = t / p.life
        q = ease_out(u)
        R, w, A = 0.0, BAND_W, band_amplitude(q)
        if p.kind == "think":
            R = 24 + 560 * q
        elif p.kind == "needs":
            R = 24 + 900 * q
        elif p.kind == "done":
            R, w = 24 + 1400 * q, 48.0
        elif p.kind == "view_on":
            R = 320 * q
        elif p.kind == "view_near":
            R = p.reach * q
        elif p.kind == "far":
            R = 30 + 300 * q
        else:  # the reduced-motion fades: one bump of the same tiles, in place
            w = {"done_fade": 80.0, "view_fade": 40.0}.get(p.kind, 60.0)
            A = math.sin(math.pi * min(1.0, u))
        return Effect(p.group, p.space, p.geom, R, w, max(0.0, A), p.colour, p.view)

    def _held(self, now: float) -> list[Effect]:
        """The static terms: the needs glow, reduced motion's thinking glow, the
        selection footprints."""
        out = []
        x, y = self.agent_anchor
        anchor = (x, y, x, y)
        if self._needs_since is not None:
            a = GLOW_A * ease_out((now - self._needs_since) / 0.3)
            out.append(Effect("agent", "screen", anchor, 0.0, GLOW_W, a, "agent"))
        elif self._needs_until is not None and now < self._needs_until + 0.2:
            a = GLOW_A * (1 - ease_in((now - self._needs_until) / 0.2))
            out.append(Effect("agent", "screen", anchor, 0.0, GLOW_W, a, "agent"))
        if self.reduced_motion:
            if self._busy_since is not None:
                a = GLOW_A * ease_out((now - self._busy_since) / FADE_S)
                out.append(Effect("agent", "screen", anchor, 0.0, GLOW_W, a, "agent"))
            elif self._busy_until is not None and now < self._busy_until + FADE_S:
                a = GLOW_A * (1 - ease_in((now - self._busy_until) / FADE_S))
                out.append(Effect("agent", "screen", anchor, 0.0, GLOW_W, a, "agent"))
        for vid, tie in self._ties.items():
            a = ease_out((now - tie.since) / 0.2)
            if tie.until is not None:
                a = min(a, 1 - ease_in((now - tie.until) / 0.2))
            if a > 0:
                x0, y0, w, h = tie.rect
                geom = (x0, y0, x0 + w, y0 + h)
                out.append(
                    Effect("tie", tie.space, geom, 0.0, SELECT_W, SELECT_A * a, tie.chain, view=vid)
                )
        return out

    def _prune(self, now: float) -> None:
        self._pulses = [p for p in self._pulses if p.start + p.life > now]
        self._ties = {
            v: t
            for v, t in self._ties.items()
            if t.until is None or t.dragged or now < t.until + 0.2
        }
        if self._busy_until is not None and now >= self._busy_until + FADE_S:
            self._busy_until = None
        if self._needs_until is not None and now >= self._needs_until + 0.2:
            self._needs_until = None

    def effects(self, now: float) -> list[Effect]:
        self._prune(now)
        pulses = sorted(self._live_pulses(now), key=lambda p: p.start)
        agent = [self._pulse_effect(p, now) for p in pulses if p.group == "agent"]
        held = self._held(now)
        agent += [e for e in held if e.group == "agent"]
        ties = [e for e in held if e.group == "tie"]
        # view pulses: the newest event first, its bands in order
        views = sorted((p for p in pulses if p.group == "view"), key=lambda p: (-p.born, p.start))
        out = agent + ties + [self._pulse_effect(p, now) for p in views]
        return out[:MAX_EFFECTS]

    def animating(self, now: float) -> bool:
        """Something changes from frame to frame: the clock must tick."""
        if self._live_pulses(now):
            return True
        fades = [(self._needs_since, 0.3), (self._needs_until, 0.2)]
        if self.reduced_motion:
            fades += [(self._busy_since, FADE_S), (self._busy_until, FADE_S)]
        for tie in self._ties.values():
            fades += [(tie.since, 0.2), (tie.until, 0.2)]
        return any(t0 is not None and t0 <= now < t0 + d for t0, d in fades)

    def next_change(self, now: float) -> float | None:
        """When a resting floor starts changing again (a needs-attention cycle, a pulse
        scheduled ahead), or None."""
        starts = [p.start for p in self._pulses if p.start > now]
        if self._needs_since is not None and not self.reduced_motion:
            n0, every = self._needs_since, NEEDS_PERIOD_S
            starts.append(n0 + every * (math.floor((now - n0) / every + 1e-9) + 1))
        return min(starts) if starts else None


__all__ = [
    "BAND_W",
    "CELL_TEXELS",
    "MAX_EFFECTS",
    "MAX_PULSE_DISTANCE",
    "RES",
    "SELECT_A",
    "SELECT_W",
    "THINK_PERIOD_S",
    "Chip",
    "Effect",
    "FloorCues",
    "FloorGrid",
    "Shown",
    "band_amplitude",
    "band_profile",
    "distance_text",
    "ease_out",
    "floor_grid",
    "texel_px",
    "tile_side",
]
