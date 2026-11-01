"""Item key <-> time for one collection (contracts section 6, ``time``; HLD 3.8 timelines).

A ``time`` value is ``{t, timeline}``. On the ``index`` timeline ``t`` is an item (or
frame) index; on any other timeline it is seconds on a clock the collection declares
(``col.timeline``, plus ``col.time_offset`` seconds). :class:`TimeMap` is the thin layer
the gallery, json and video hosts call: it turns an index into the time to publish and a
received time into the nearest index, and says which timelines it can answer.

Where an item's stamp comes from (first hit wins, all items must have one or the
collection has no clock and only answers ``index``):

1. ``item.meta["stamp_ns"]`` / ``["stamp_s"]`` (an intake that knows);
2. the keys regex groups ``stamp_ns`` / ``stamp`` (the ``frame_stamp`` preset);
3. a 13-19 digit run in the primary file name (``00042_1700000000000000000.jpg``),
   read as ns / us / ms / s by magnitude.

Videos carry their stamps in a sidecar ``<name>.timeline.json`` (``tools/make_testdata.py``
writes one: ``frames[{frame, key, stamp_ns, pts}]``); without it a video's clock is its
pts. A collection left on ``index`` whose items do have stamps publishes on the default
clock ``stamp`` and answers any named timeline (the stamps are the only clock it has),
so the human path works without declaring anything; declaring is for disambiguation
(two clocks in one canvas) and the offset.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from specto_core.kinds import Item
from specto_core.timebase import (  # the stamp rules live in core (the global clock)
    DEFAULT_CLOCK,
    INDEX,
    item_stamp,
    load_sidecar,
    stamp_seconds,
)

TIME = "time"
HOVER_TIME = "hover_time"
MAX_TICKS = 4000


@dataclass
class TimeMap:
    keys: list[str]
    #: seconds on the collection's own clock, by index (None: the items have no stamps)
    stamps: list[float] | None = None
    #: the clock the stamps are on; ``index`` means "not declared"
    timeline: str = INDEX
    offset: float = 0.0
    #: video only: container pts per frame, for exact seeks
    pts: list[float] | None = None
    _order: list[tuple[float, int]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.stamps is not None and len(self.stamps) != len(self.keys):
            raise ValueError("stamps and keys differ in length")
        self._order = sorted((s, i) for i, s in enumerate(self.stamps or []))

    # -- construction --------------------------------------------------------------------
    @classmethod
    def from_items(
        cls, items: list[Item], *, timeline: str = INDEX, offset: float = 0.0
    ) -> TimeMap:
        keys = [it.key for it in items]
        stamps: list[float] | None = []
        for it in items:
            s = item_stamp(it)
            if s is None:
                stamps = None
                break
            stamps.append(s)
        return cls(keys, stamps, timeline or INDEX, float(offset or 0.0))

    @classmethod
    def from_video(
        cls,
        path: str | Path,
        *,
        fps: float = 0.0,
        frame_count: int = 0,
        timeline: str = INDEX,
        offset: float = 0.0,
    ) -> TimeMap:
        """Frames of a video: the sidecar's stamps and pts when present, else pts from
        ``fps`` and ``frame_count`` (the video's own clock)."""
        side = load_sidecar(path)
        if side:
            frames = side["frames"]
            keys = [str(f.get("key", f.get("frame", i))) for i, f in enumerate(frames)]
            stamps = [stamp_seconds(f.get("stamp_ns", f.get("stamp"))) for f in frames]
            pts = [
                float(f["pts"]) if f.get("pts") is not None else i / (fps or 30.0)
                for i, f in enumerate(frames)
            ]
            if all(s is not None for s in stamps):
                tl = timeline if timeline != INDEX else str(side.get("timeline") or DEFAULT_CLOCK)
                return cls(keys, [float(s) for s in stamps], tl, float(offset or 0.0), pts)
            return cls(keys, pts, timeline or INDEX, float(offset or 0.0), pts)
        n = max(0, int(frame_count))
        pts = [i / fps if fps > 0 else float(i) for i in range(n)]
        return cls([str(i) for i in range(n)], pts, timeline or INDEX, float(offset or 0.0), pts)

    # -- queries -------------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.keys)

    @property
    def has_clock(self) -> bool:
        return self.stamps is not None and len(self.stamps) > 0

    def supports(self, timeline: str) -> bool:
        """Can a time on ``timeline`` be mapped to an item? ``index`` always; a named
        timeline when it is the declared one, or the collection declares none."""
        if timeline == INDEX:
            return True
        return self.has_clock and self.timeline in (INDEX, timeline)

    def publish_timeline(self, prefer: str | None = None) -> str:
        """The timeline a view over this collection publishes on: the declared one, else
        ``prefer`` (what the link already carries), else the default clock when the
        items have stamps, else ``index``."""
        if not self.has_clock:
            return INDEX
        if self.timeline != INDEX:
            return self.timeline
        if prefer and prefer != INDEX:
            return prefer
        return DEFAULT_CLOCK

    def time_of(self, index: int, timeline: str) -> float | None:
        if index < 0 or index >= len(self.keys):
            return None
        if timeline == INDEX:
            return float(index)
        if not self.supports(timeline):
            return None
        return self.stamps[index] + self.offset  # type: ignore[index]

    def value_of(self, index: int, timeline: str) -> dict[str, Any] | None:
        t = self.time_of(index, timeline)
        return None if t is None else {"t": t, "timeline": timeline}

    def index_at(self, t: float, timeline: str) -> int:
        """Nearest index for ``t`` on ``timeline``; -1 when it cannot be mapped."""
        if not self.keys or not self.supports(timeline):
            return -1
        if timeline == INDEX:
            return max(0, min(len(self.keys) - 1, round(t)))
        s = float(t) - self.offset
        order = self._order
        i = bisect.bisect_left(order, (s, -1))
        cands = [j for j in (i - 1, i) if 0 <= j < len(order)]
        best = min(cands, key=lambda j: abs(order[j][0] - s))
        return order[best][1]

    def index_for(self, value: dict[str, Any] | None) -> int:
        if not value or "t" not in value:
            return -1
        try:
            return self.index_at(float(value["t"]), str(value.get("timeline") or INDEX))
        except (TypeError, ValueError):
            return -1

    def key_at(self, index: int) -> str | None:
        return self.keys[index] if 0 <= index < len(self.keys) else None

    def index_of(self, key: str) -> int:
        try:
            return self.keys.index(key)
        except ValueError:
            return -1

    def range(self, timeline: str) -> tuple[float, float] | None:
        if not self.keys or not self.supports(timeline):
            return None
        if timeline == INDEX:
            return 0.0, float(len(self.keys) - 1)
        return self._order[0][0] + self.offset, self._order[-1][0] + self.offset

    def ticks(self, timeline: str, limit: int = MAX_TICKS) -> list[float]:
        """Item times in order, thinned to ``limit`` for drawing."""
        if not self.keys or not self.supports(timeline):
            return []
        if timeline == INDEX:
            vals = [float(i) for i in range(len(self.keys))]
        else:
            vals = [s + self.offset for s, _ in self._order]
        if len(vals) > limit:
            step = len(vals) / limit
            vals = [vals[int(k * step)] for k in range(limit)]
        return vals


def collection_timeline(canvas: Any, col_id: str | None) -> tuple[str, float]:
    """``(timeline, offset)`` a collection declares (``index``, 0 when unknown)."""
    if not col_id:
        return INDEX, 0.0
    try:
        col = canvas.get(col_id, "col")
    except Exception:
        return INDEX, 0.0
    return str(col.get("timeline") or INDEX), float(col.get("time_offset") or 0.0)


__all__ = [
    "DEFAULT_CLOCK",
    "HOVER_TIME",
    "INDEX",
    "MAX_TICKS",
    "TIME",
    "TimeMap",
    "collection_timeline",
    "item_stamp",
    "load_sidecar",
    "stamp_seconds",
]
