"""Per-canvas quotas and view health (HLD 4.3-4.4; glossary: quota).

A quota is a ceiling on costly live views. In the canvas document (v2) a view is live
when its layout rect touches the window frame, or when it floats (ADR 0021: the float
layer is always on screen); off-screen views are paused and cost nothing, and so are
collapsed ones (#114: a title bar renders no content).
``quota_problems`` judges a submitted document, whoever submits it, and names the views
inside the frame that count.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from .canvas_document import Problem
from .float_layer import is_floating
from .kinds import ViewCost

QUOTA_NAMES = ("web_views", "viewports3d", "video_decoders", "resident_points", "rtx_views")

#: ``view_cost(view type) -> ViewCost | None`` (None: a type without a manifest, free)
ViewCostFn = Callable[[str], ViewCost | None]


class Quotas(BaseModel):
    model_config = ConfigDict(extra="forbid")
    web_views: int = 8
    viewports3d: int = 3
    resident_points: int = 20_000_000
    video_decoders: int = 4
    rtx_views: int = 1  # #132: one live RTX view per canvas


def is_collapsed(view: dict[str, Any]) -> bool:
    """Shown as its title bar alone (#114): nothing renders, so it costs nothing."""
    return bool((view.get("layout") or {}).get("collapsed"))


def view_cost_of(view: dict[str, Any], cost: ViewCost) -> ViewCost:
    """What this view costs, from its type's cost and its params: a view that asks for
    the Storm renderer costs no RTX view (#132)."""
    if cost.rtx_views and (view.get("params") or {}).get("renderer") == "storm":
        return cost.model_copy(update={"rtx_views": 0})
    return cost


def in_window_frame(doc: dict[str, Any], view: dict[str, Any]) -> bool:
    """Whether a view's layout rect touches the document's window frame; a floating
    view always does."""
    if is_floating(view):
        return True
    rect = doc["window_frame"]["rect"]
    (x, y), (w, h) = view["layout"]["position"], view["layout"]["size"]
    return (
        x < rect["x"] + rect["w"]
        and x + w > rect["x"]
        and y < rect["y"] + rect["h"]
        and y + h > rect["y"]
    )


def _usage(
    doc: dict[str, Any], view_cost: ViewCostFn
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Per quota: what the live views use, and which views count (document order)."""
    used: dict[str, int] = dict.fromkeys(QUOTA_NAMES, 0)
    counted: dict[str, list[str]] = {name: [] for name in QUOTA_NAMES}
    for vid, view in (doc.get("views") or {}).items():
        cost = view_cost(view["type"])
        if cost is None or is_collapsed(view) or not in_window_frame(doc, view):
            continue
        cost = view_cost_of(view, cost)
        for name in QUOTA_NAMES:
            n = getattr(cost, name)
            if n > 0:
                used[name] += n
                counted[name].append(vid)
    return used, counted


def make_room(
    doc: dict[str, Any], quotas: Quotas, view_cost: ViewCostFn, keep: str
) -> dict[str, str]:
    """Collapse live views in ``doc`` (in place) until no quota is exceeded, never
    ``keep`` (the view a user gesture is making live, SPEC-34): per exceeded quota the
    oldest counted view first (document order), floating ones last (the user keeps
    those in sight on purpose). A collapsed view is paused and costs nothing, its links
    keep delivering, and expanding it is the user's to do. Returns each collapsed view
    with the quota it made room under; a quota collapsing cannot meet (``keep`` alone
    exceeds it) is left for ``quota_problems`` to report."""
    collapsed: dict[str, str] = {}
    while True:
        used, counted = _usage(doc, view_cost)
        over = [n for n in QUOTA_NAMES if used[n] > getattr(quotas, n)]
        pick = None
        for name in over:
            others = [v for v in counted[name] if v != keep]
            others.sort(key=lambda v: is_floating(doc["views"][v]))  # stable: oldest first
            if others:
                pick = (others[0], name)
                break
        if pick is None:
            return collapsed
        doc["views"][pick[0]]["layout"]["collapsed"] = True
        collapsed[pick[0]] = pick[1]


def quota_problems(doc: dict[str, Any], quotas: Quotas, view_cost: ViewCostFn) -> list[Problem]:
    """One problem per quota the document's live views would exceed."""
    used, counted = _usage(doc, view_cost)
    out: list[Problem] = []
    frame = doc["window_frame"]["rect"]
    for name in QUOTA_NAMES:
        limit = getattr(quotas, name)
        if used[name] <= limit:
            continue
        ids = counted[name]
        floats = [v for v in ids if is_floating(doc["views"][v])]
        types = sorted({doc["views"][v]["type"] for v in ids})
        span = (
            f"x {frame['x']}..{frame['x'] + frame['w']}, y {frame['y']}..{frame['y'] + frame['h']}"
        )
        out.append(
            Problem(
                path="/views",
                message=f"quota {name}={limit} would be exceeded: {used[name]} inside the "
                f"window frame ({', '.join(ids)}; types {', '.join(types)})",
                fix=f"remove one of {ids}, collapse it (layout.collapsed: true), or move "
                f"it outside the window frame ({span}): either way it is paused and costs "
                "nothing"
                + (
                    f"; floating views ({', '.join(floats)}) always count: unfloat one first"
                    if floats
                    else ""
                )
                + (
                    "; or set params.renderer: storm on one (Hydra Storm costs no RTX view)"
                    if name == "rtx_views"
                    else ""
                ),
            )
        )
    return out
