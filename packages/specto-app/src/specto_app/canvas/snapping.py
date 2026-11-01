"""Snapping and culling for the normal view (spec #35 stories 64, 65; glossary
"Snapping", "Off-screen"). Qt-free, so the overworld (#45) can resolve its positions
through the same functions.

Rects are ``(x, y, w, h)`` in canvas pixels at 1:1. A move pulls the view's left/right
and top/bottom borders flush against the nearest neighbour border or window-frame edge
within ``threshold`` pixels, one pull per axis; a resize does the same for the edges
being dragged and never lets the view shrink below ``MIN_SIZE``. Specto does not dock
or tile: snapping removes gaps, nothing more.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from specto_app.theme import METRICS

Rect = tuple[float, float, float, float]
Guide = tuple[str, float]  # ("x", 280.0): a vertical line at x = 280

SNAP_PX = 10.0
MIN_SIZE = (200.0, 120.0)
#: the transparent band inside a view's rect that its frame is drawn in (theme metric
#: ``gutter``): snapping keeps document rects flush, frames show a 2 * GUTTER gap
GUTTER = float(METRICS["gutter"])


@dataclass(frozen=True)
class Snapped:
    rect: Rect
    guides: tuple[Guide, ...]


def _lines(
    rects: Mapping[str, Rect] | Iterable[Rect], frame: Rect | None, exclude: str | None
) -> tuple[list[float], list[float]]:
    """Every border other views and the frame offer: x lines and y lines."""
    xs: list[float] = []
    ys: list[float] = []
    items = rects.items() if isinstance(rects, Mapping) else enumerate(rects)
    for key, (x, y, w, h) in items:
        if exclude is not None and key == exclude:
            continue
        xs += [x, x + w]
        ys += [y, y + h]
    if frame is not None:
        fx, fy, fw, fh = frame
        xs += [fx, fx + fw]
        ys += [fy, fy + fh]
    return xs, ys


def _pull(
    edges: Iterable[float], lines: Iterable[float], threshold: float
) -> tuple[float, float] | None:
    """The smallest shift that puts one of ``edges`` on one of ``lines``, as
    ``(delta, line)``; None when every line is further than ``threshold``."""
    best: tuple[float, float] | None = None
    for e in edges:
        for ln in lines:
            d = ln - e
            if abs(d) <= threshold and (best is None or abs(d) < abs(best[0])):
                best = (d, ln)
    return best


def snap_move(
    rect: Rect,
    rects: Mapping[str, Rect] | Iterable[Rect],
    frame: Rect | None,
    *,
    threshold: float = SNAP_PX,
    exclude: str | None = None,
) -> Snapped:
    x, y, w, h = rect
    xs, ys = _lines(rects, frame, exclude)
    guides: list[Guide] = []
    px = _pull((x, x + w), xs, threshold)
    if px is not None:
        x += px[0]
        guides.append(("x", px[1]))
    py = _pull((y, y + h), ys, threshold)
    if py is not None:
        y += py[0]
        guides.append(("y", py[1]))
    return Snapped((x, y, w, h), tuple(guides))


def snap_resize(
    rect: Rect,
    edges: Iterable[str],
    rects: Mapping[str, Rect] | Iterable[Rect],
    frame: Rect | None,
    *,
    threshold: float = SNAP_PX,
    exclude: str | None = None,
    min_size: tuple[float, float] = MIN_SIZE,
) -> Snapped:
    """``edges`` are the borders being dragged (``left``, ``right``, ``top``, ``bottom``);
    the opposite borders stay where they are."""
    x, y, w, h = rect
    edges = set(edges)
    xs, ys = _lines(rects, frame, exclude)
    guides: list[Guide] = []
    left, right, top, bottom = x, x + w, y, y + h
    if "left" in edges:
        p = _pull((left,), xs, threshold)
        if p is not None:
            left += p[0]
            guides.append(("x", p[1]))
    elif "right" in edges:
        p = _pull((right,), xs, threshold)
        if p is not None:
            right += p[0]
            guides.append(("x", p[1]))
    if "top" in edges:
        p = _pull((top,), ys, threshold)
        if p is not None:
            top += p[0]
            guides.append(("y", p[1]))
    elif "bottom" in edges:
        p = _pull((bottom,), ys, threshold)
        if p is not None:
            bottom += p[0]
            guides.append(("y", p[1]))
    # the minimum size wins over the snap, taken from the edge being dragged
    if right - left < min_size[0]:
        if "left" in edges:
            left = right - min_size[0]
        else:
            right = left + min_size[0]
    if bottom - top < min_size[1]:
        if "top" in edges:
            top = bottom - min_size[1]
        else:
            bottom = top + min_size[1]
    return Snapped((left, top, right - left, bottom - top), tuple(guides))


def drawn_rect(rect: Rect, gutter: float = GUTTER) -> Rect:
    """Where a view's frame is drawn: its document rect inset by the gutter."""
    x, y, w, h = rect
    return (x + gutter, y + gutter, w - 2 * gutter, h - 2 * gutter)


def overlaps(a: Rect, b: Rect) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def visible_ids(viewport: Rect, rects: Mapping[str, Rect]) -> set[str]:
    """The views whose rect overlaps the viewport: these render; the rest pause."""
    return {vid for vid, r in rects.items() if overlaps(viewport, r)}


__all__ = [
    "GUTTER",
    "MIN_SIZE",
    "SNAP_PX",
    "Guide",
    "Rect",
    "Snapped",
    "drawn_rect",
    "overlaps",
    "snap_move",
    "snap_resize",
    "visible_ids",
]
