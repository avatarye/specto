"""The walk-through's camera and motion rules (#174, design page 06 "motion notes"),
Qt-free: where the camera goes for a hop, how long a move takes and how it eases,
which pulse dots an intaker drops.

Two camera levels only: hops at 1:1 in the normal view (the canvas pan), the
establishing shot and the summary in the overworld (a zoom and a screen offset, screen =
pan + canvas * zoom). Area px are the canvas area's: the normal view puts canvas point
``pan`` at the area's padded corner ``(PAD, PAD)``."""

from __future__ import annotations

import math

Rect = tuple[float, float, float, float]

SAFE_TOP = 24.0  # the safe frame: the area minus 24 px at the top ...
SAFE_BOTTOM = 190.0  # ... and the bottom band the caption card and the controls take
ZOOM_MS = 700  # a combined pan + zoom move (normal view <-> overworld)
EXIT_MS = 500  # Esc, x or Done: back to where the user was, one move
FADE_MS = 220  # focus rings cross-fade after the camera settles; dims fade out on exit
CUT_MS = 150  # reduced motion: the camera cuts with this cross-fade
EDGE = 16.0  # a hop too big for the frame keeps this much room before its start
FIT_MARGIN = 48.0  # the overworld shots keep this much room round the path
CHIP_ROOM = 34.0  # the summary hangs a chip of counts under each view
# the pulse: dots running out port to in port at a constant screen speed and size
PULSE_SPEED = 0.22  # px per ms (220 px/s)
PULSE_EVERY_MS = 180
PULSE_MAX_DOTS = 12


def ease(t: float) -> float:
    """cubic-bezier(0.65, 0, 0.35, 1), the walk's ease-in-out, at progress ``t``."""
    t = min(1.0, max(0.0, t))
    x1, y1, x2, y2 = 0.65, 0.0, 0.35, 1.0

    def bez(a: float, b: float, s: float) -> float:
        return 3 * a * s * (1 - s) ** 2 + 3 * b * s * s * (1 - s) + s**3

    lo, hi = 0.0, 1.0
    for _ in range(40):  # the curve's x is monotonic: bisect for the s that gives t
        mid = (lo + hi) / 2
        if bez(x1, x2, mid) < t:
            lo = mid
        else:
            hi = mid
    return bez(y1, y2, (lo + hi) / 2)


def pan_ms(distance: float) -> int:
    """A hop pan at 1:1: 300 ms + 0.5 ms per px, between 400 and 900 ms."""
    return int(min(900.0, max(400.0, 300.0 + 0.5 * abs(distance))))


def safe_frame(area_w: float, area_h: float) -> Rect:
    """The part of the area a hop must sit in, in area px."""
    return (0.0, SAFE_TOP, area_w, max(1.0, area_h - SAFE_TOP - SAFE_BOTTOM))


def union(rects: list[Rect]) -> Rect:
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return (x0, y0, x1 - x0, y1 - y0)


def hop_pan(
    pan: tuple[float, float],
    area: tuple[float, float],
    pad: float,
    hop: list[Rect],
) -> tuple[float, float]:
    """The 1:1 pan that shows a hop: its views' rects (canvas px, the target last)
    inside the safe frame. A view bigger than the frame counts as its top-left part (its
    header and ports), as much as fits. Hold when the hop already fits where it is (fewer
    moves are calmer); otherwise centre it in the safe frame on each axis it fits. On an
    axis it does not fit (a long link), the target is what must show: it sits 16 px in
    from the frame's far side, the source running off the near one."""
    sx, sy, sw, sh = safe_frame(*area)
    cw, ch = max(1.0, sw - 2 * EDGE), max(1.0, sh - 2 * EDGE)
    rects = [(x, y, min(w, cw), min(h, ch)) for x, y, w, h in hop]
    ux, uy, uw, uh = union(rects)
    # canvas point c sits at area px pad + c - pan
    left, top = pad + ux - pan[0], pad + uy - pan[1]
    if left >= sx and top >= sy and left + uw <= sx + sw and top + uh <= sy + sh:
        return pan
    tx, ty, tw, th = rects[-1]

    def axis(start: float, size: float, t0: float, tsize: float, s0: float, span: float) -> float:
        if size <= span:
            return pad + start + size / 2 - (s0 + span / 2)
        if t0 + tsize >= start + size:  # the target is the far end: align its far side
            return pad + t0 + tsize - (s0 + span - EDGE)
        return pad + t0 - (s0 + EDGE)

    return (round(axis(ux, uw, tx, tw, sx, sw)), round(axis(uy, uh, ty, th, sy, sh)))


def hop_fits(area: tuple[float, float], hop: list[Rect]) -> bool:
    """Whether a hop's views (a view bigger than the frame as its top-left part) fit in
    the safe frame together; one that does not is a long link the camera follows."""
    _, _, sw, sh = safe_frame(*area)
    cw, ch = max(1.0, sw - 2 * EDGE), max(1.0, sh - 2 * EDGE)
    _, _, uw, uh = union([(x, y, min(w, cw), min(h, ch)) for x, y, w, h in hop])
    return uw <= sw and uh <= sh


def fit_over(bounds: Rect, area: tuple[float, float]) -> tuple[float, float, float]:
    """The overworld shot of ``bounds`` (canvas px) inside the safe frame: ``(zoom,
    panX, panY)``, never past 1:1, with room under the views for the summary's chips."""
    sx, sy, sw, sh = safe_frame(*area)
    sh = max(1.0, sh - CHIP_ROOM)
    bx, by, bw, bh = bounds
    zoom = min(1.0, (sw - 2 * FIT_MARGIN) / max(1.0, bw), (sh - FIT_MARGIN) / max(1.0, bh))
    zoom = max(0.05, zoom)
    px = sx + (sw - bw * zoom) / 2 - bx * zoom
    py = sy + (sh - bh * zoom) / 2 - by * zoom
    return (zoom, px, py)


def over_of_pan(pan: tuple[float, float], pad: float) -> tuple[float, float, float]:
    """The overworld transform that shows exactly what the normal view shows at
    ``pan``: zoom 1, the same canvas point at the same area px."""
    return (1.0, pad - pan[0], pad - pan[1])


def zoom_between(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    t: float,
    area: tuple[float, float],
) -> tuple[float, float, float]:
    """One combined pan + zoom move from overworld transform ``a`` to ``b`` at eased
    progress ``t``: log(zoom) interpolates, so the zoom rate feels constant, and the
    canvas point at the area's centre moves straight between the two shots' centres, so
    the move never zooms then pans."""
    (za, pxa, pya), (zb, pxb, pyb) = a, b
    cx, cy = area[0] / 2, area[1] / 2
    ca = ((cx - pxa) / za, (cy - pya) / za)
    cb = ((cx - pxb) / zb, (cy - pyb) / zb)
    z = math.exp(math.log(za) + (math.log(zb) - math.log(za)) * t)
    c = (ca[0] + (cb[0] - ca[0]) * t, ca[1] + (cb[1] - ca[1]) * t)
    return (z, cx - c[0] * z, cy - c[1] * z)


def pulse_every_ms(length: float) -> float:
    """One dot every 180 ms, slower on a long wire so at most 12 run on it at once."""
    travel = max(0.0, length) / PULSE_SPEED
    return max(float(PULSE_EVERY_MS), travel / PULSE_MAX_DOTS)


def dropped(ratio: float, k: int) -> bool:
    """Whether the intaker drops pulse dot ``k``: spread evenly so that over any run
    of dots the kept share is ``ratio`` (128 of 412 keeps about 1 in 3; 1 of 412 keeps
    the first and drops the rest of a hop's dots). A negative ratio drops none."""
    if ratio < 0:
        return False
    r = min(1.0, ratio)
    if k == 0:
        return r <= 0
    return math.floor((k + 1) * r) == math.floor(k * r)


__all__ = [
    "CUT_MS",
    "EXIT_MS",
    "FADE_MS",
    "PULSE_EVERY_MS",
    "PULSE_MAX_DOTS",
    "PULSE_SPEED",
    "ZOOM_MS",
    "dropped",
    "ease",
    "fit_over",
    "hop_fits",
    "hop_pan",
    "over_of_pan",
    "pan_ms",
    "pulse_every_ms",
    "safe_frame",
    "union",
    "zoom_between",
]
