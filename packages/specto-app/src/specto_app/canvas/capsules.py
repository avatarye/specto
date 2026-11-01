"""Port capsules (#160; glossary: port capsule). Qt-free.

The two ports a link joins show the same identicon (#81, #92): the source's output
circle shows the source's own, the target's input circle the upstream view's, the
source again. ``port_pairs`` lists those pairs in canvas px (``chains.port_anchor``), one
per link between two views on the canvas, and a second (``<link>:back``) for a two-way
link, whose target's output feeds the input circle it takes on its source. The project
node has no port circle (#127) and pairs nothing.

When a pair's circles come within ``JOIN_PX`` on screen a solid capsule joins them; it
holds until they pass ``BREAK_PX`` (hysteresis), so a view dragged past its partner, or
snapping by a few px, does not flicker it. ``joined_after`` is that step, pure;
``CapsuleTracker`` keeps the joined set across the steps of a drag.

The distances, between the circles' centres in screen px: a 20 px circle joins its
partner within 32 px, a gap of at most 12 px (the canvas padding) between the two rings,
where two circles already read as one pair; snapped views (4 px apart) and the worked
example's 20 px spacing (24 px) join. It splits past 56 px: 24 px more pull than the
join, over twice the 10 px snap, so a snapping drag never flickers it, and a gap of
36 px reads as two separate ports again.
"""

from __future__ import annotations

import math
from typing import Any

from specto_core.ports import PROJECT_ID

from .chains import Rect, port_anchor

#: the circles' centres join within this many screen px
JOIN_PX = 32.0
#: a joined pair splits when its centres are further apart than this, screen px
BREAK_PX = 56.0
#: the second pair of a two-way link: ``<link id>`` + this
BACK = ":back"


def port_pairs(rects: dict[str, Rect], links: dict[str, Any]) -> list[dict[str, Any]]:
    """``{id, link, outView, inView, inIndex, x1, y1, x2, y2}`` per pair, in document
    order: ``(x1, y1)`` the output circle's centre, ``(x2, y2)`` the input circle's, canvas
    px; ``inIndex`` is the input circle's place in ``inView``'s stack (``chains.
    input_indices``, ``ports_info.input_ports``: one circle per link in, a two-way link
    taking one on its source too, in document order)."""
    counts: dict[str, int] = {}
    out: list[dict[str, Any]] = []

    def pair(pid: str, lid: str, src: str, dst: str, index: int) -> None:
        if src == PROJECT_ID or src not in rects or dst not in rects:
            return
        x1, y1 = port_anchor(rects[src], "out")
        x2, y2 = port_anchor(rects[dst], "in", index)
        out.append(
            {
                "id": pid,
                "link": lid,
                "outView": src,
                "inView": dst,
                "inIndex": index,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
            }
        )

    for lid, link in links.items():
        s, t = link["source"]["view"], link["target"]["view"]
        index = counts.get(t, 0)
        counts[t] = index + 1
        pair(lid, lid, s, t, index)
        if link.get("two_way"):
            back = counts.get(s, 0)
            counts[s] = back + 1
            pair(lid + BACK, lid, t, s, back)
    return out


def span(pair: dict[str, Any]) -> float:
    """The distance between a pair's circle centres, canvas px."""
    return math.hypot(pair["x2"] - pair["x1"], pair["y2"] - pair["y1"])


def joined_after(
    joined: set[str],
    pairs: list[dict[str, Any]],
    *,
    ui: float = 1.0,
    join_px: float = JOIN_PX,
    break_px: float = BREAK_PX,
) -> set[str]:
    """The pairs joined after this step: those within ``join_px`` on screen, and those
    joined before still within ``break_px``. ``ui`` is canvas px per screen px (1 in the
    normal view). A pair no longer listed leaves."""
    out: set[str] = set()
    for p in pairs:
        reach = break_px if p["id"] in joined else join_px
        if span(p) / ui <= reach:
            out.add(p["id"])
    return out


class CapsuleTracker:
    """The joined pairs of one layer across the steps of its drags."""

    def __init__(self) -> None:
        self.joined: set[str] = set()

    def update(self, pairs: list[dict[str, Any]], ui: float = 1.0) -> bool:
        """Step with this layout's pairs; True when the joined set changed."""
        joined = joined_after(self.joined, pairs, ui=ui)
        if joined == self.joined:
            return False
        self.joined = joined
        return True


__all__ = [
    "BACK",
    "BREAK_PX",
    "JOIN_PX",
    "CapsuleTracker",
    "joined_after",
    "port_pairs",
    "span",
]
