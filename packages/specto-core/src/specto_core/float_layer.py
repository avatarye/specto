"""The float layer (ADR 0021; glossary: float, floating view, float layer).

A floating view sits fixed in the window above the pannable canvas. The document keeps
its place as an offset from the nearest window corner (``layout.floating``), so it
stays put when the window resizes. Window px here are the window frame's own: (0, 0)
is its top-left, (w, h) its size (the canvas area inside its padding). Qt-free, so the
gestures, the quotas and the app's float layer share one geometry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

Point = tuple[float, float]
Size = tuple[float, float]

CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")
#: a collapsed view's height (#114: its title bar), whatever its stored size; the app's
#: ``collapsed_h`` theme metric
COLLAPSED_H = 36.0


def is_floating(view: Mapping[str, Any]) -> bool:
    return bool((view.get("layout") or {}).get("floating"))


def floating_ids(doc: Mapping[str, Any]) -> list[str]:
    """The views on the float layer, in document order."""
    return [vid for vid, v in (doc.get("views") or {}).items() if is_floating(v)]


def shown_size(layout: Mapping[str, Any]) -> Size:
    """The size a view takes in the window: its stored size, or its title bar's at the
    same width while it is collapsed (what anchoring and keeping it inside go by)."""
    w, h = (float(v) for v in layout["size"])
    return (w, COLLAPSED_H) if layout.get("collapsed") else (w, h)


def frame_size(doc: Mapping[str, Any]) -> Size:
    rect = doc["window_frame"]["rect"]
    return (float(rect["w"]), float(rect["h"]))


def frame_origin(doc: Mapping[str, Any]) -> Point:
    rect = doc["window_frame"]["rect"]
    return (float(rect["x"]), float(rect["y"]))


def clamp_into(pos: Point, size: Size, window: Size) -> Point:
    """``pos`` moved just enough to keep a view of ``size`` inside the window (its
    top-left wins when the view is bigger than the window)."""
    x = min(max(float(pos[0]), 0.0), max(0.0, window[0] - size[0]))
    y = min(max(float(pos[1]), 0.0), max(0.0, window[1] - size[1]))
    return (x, y)


def anchor_at(pos: Point, size: Size, window: Size) -> dict[str, Any]:
    """The ``corner`` and ``offset`` of a view of ``size`` at window position ``pos``:
    the corner nearest the view's centre, the offset from it to the view's matching
    corner, inward. The view is kept inside the window first."""
    x, y = clamp_into(pos, size, window)
    w, h = size
    right = x + w / 2 > window[0] / 2
    bottom = y + h / 2 > window[1] / 2
    corner = ("bottom_" if bottom else "top_") + ("right" if right else "left")
    dx = window[0] - (x + w) if right else x
    dy = window[1] - (y + h) if bottom else y
    return {"corner": corner, "offset": [max(0.0, dx), max(0.0, dy)]}


def window_position(floating: Mapping[str, Any], size: Size, window: Size) -> Point:
    """Where a floating view of ``size`` sits in a window of ``window`` size: its offset
    from its corner, kept inside the window when the window is too small for it."""
    corner = floating.get("corner", "top_left")
    dx, dy = (float(v) for v in floating.get("offset", (0.0, 0.0)))
    x = window[0] - size[0] - dx if corner.endswith("right") else dx
    y = window[1] - size[1] - dy if corner.startswith("bottom") else dy
    return clamp_into((x, y), size, window)


def view_window_position(doc: Mapping[str, Any], view: Mapping[str, Any]) -> Point:
    """A floating view's window position in ``doc``'s window frame."""
    lay = view["layout"]
    return window_position(lay["floating"], shown_size(lay), frame_size(doc))


def canvas_point_under(doc: Mapping[str, Any], view: Mapping[str, Any]) -> Point:
    """The canvas point under a floating view's top-left: the window frame's origin
    (where the user panned to) plus its window position."""
    ox, oy = frame_origin(doc)
    x, y = view_window_position(doc, view)
    return (ox + x, oy + y)


__all__ = [
    "COLLAPSED_H",
    "CORNERS",
    "anchor_at",
    "canvas_point_under",
    "clamp_into",
    "floating_ids",
    "frame_origin",
    "frame_size",
    "is_floating",
    "shown_size",
    "view_window_position",
    "window_position",
]
