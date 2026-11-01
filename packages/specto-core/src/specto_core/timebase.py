"""Time on the canvas (spec #35 stories 41-43; ADR 0013: the global clock is a runtime
time base, not a document field).

A ``time`` port value is :class:`TimeValue` ``(t, timeline)``: ``t`` is float64 seconds on
a named **timeline** (a clock the data was recorded on; ``stamp`` is the default clock of
anything carrying a stamp), or an item index on the ``index`` timeline. A ``time_map``
link converts a time from one timeline to another with an offset (``resolve_time_map``
in ``links.py``).

:class:`TimeTable` is the key <-> time lookup of one view's data, built once when its
collection lands (ADR 0016: lookups are resolved when inputs change, and only looked up
at runtime): the stamps of a collection's items, or the frames of a video's sidecar
``<name>.timeline.json`` (``frames[{frame, key, stamp_ns, pts}]``). An item's stamp comes
from, first hit wins: ``item.meta["stamp_ns"]`` / ``["stamp_s"]``; the keys regex groups
``stamp_ns`` / ``stamp``; a 13-19 digit run in the primary file name
(``00042_1700000000000000000.jpg``), read as ns / us / ms / s by magnitude. All items
need one, or the view has no clock.

:class:`GlobalClock` (``engine.links.global_clock``) is the shared time base: the root view's
latest time (``now``) and the tables of every view, so data recorded at different frame
rates is looked up at one instant (``nearest``), and a key becomes a time (``time_of``)
when a gallery click is to seek a player. It never drives playback.
"""

from __future__ import annotations

import bisect
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .kinds import Item

INDEX = "index"
#: the clock of anything carrying a stamp and declaring no other timeline
DEFAULT_CLOCK = "stamp"
#: no named timeline: the receiving view's own (a Python link's bare seconds, #176);
#: a player or a clocked view takes such a time on whatever timeline it runs
OWN_TIMELINE = ""
VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v")
_DIGITS = re.compile(r"(?<!\d)(\d{13,19})(?!\d)")


class TimeValue(BaseModel):
    """The ``time`` port value: seconds on a named timeline (an index on ``index``; the
    receiver's own on ``OWN_TIMELINE``)."""

    model_config = ConfigDict(extra="forbid")
    t: float
    timeline: str = DEFAULT_CLOCK


def stamp_seconds(value: Any) -> float | None:
    """An integer stamp (ns, us, ms or s by magnitude) or a float of seconds -> seconds."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        if not value.isdigit():
            try:
                value = float(value)
            except ValueError:
                return None
        else:
            value = int(value)
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        if value >= 10**17:
            return value / 1e9
        if value >= 10**14:
            return value / 1e6
        if value >= 10**11:
            return value / 1e3
        return float(value)
    return None


def item_stamp(item: Item) -> float | None:
    meta = item.meta or {}
    for k in ("stamp_ns", "stamp_s", "stamp"):
        if k in meta:
            s = stamp_seconds(meta[k])
            if s is not None:
                return s
    keys = meta.get("keys") or {}
    for k in ("stamp_ns", "stamp"):
        if k in keys:
            s = stamp_seconds(keys[k])
            if s is not None:
                return s
    for path in (item.parts or {}).values():
        m = _DIGITS.search(Path(path).stem)
        if m:
            return stamp_seconds(int(m.group(1)))
        break
    return None


def load_sidecar(video: str | Path) -> dict[str, Any] | None:
    """``<name>.timeline.json`` beside a video, or None."""
    p = Path(video)
    side = p.with_name(p.stem + ".timeline.json")
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("frames"), list) else None


def video_part(item: Item) -> str | None:
    for path in (item.parts or {}).values():
        if Path(path).suffix.lower() in VIDEO_EXTS:
            return path
    return None


class TimeTable:
    """Key <-> time of one view's data on one timeline; ``nearest`` is a binary search
    (ties go to the earliest item, so twin frames sharing a stamp pick the first)."""

    def __init__(self, keys: list[str], times: list[float], timeline: str = DEFAULT_CLOCK):
        if len(keys) != len(times):
            raise ValueError("keys and times differ in length")
        self.keys = list(keys)
        self.times = [float(t) for t in times]
        self.timeline = timeline
        self._by_key = {k: t for k, t in zip(self.keys, self.times, strict=True)}
        self._order = sorted((t, i) for i, t in enumerate(self.times))

    def __len__(self) -> int:
        return len(self.keys)

    def __repr__(self) -> str:
        return f"TimeTable({len(self.keys)} keys on {self.timeline!r})"

    # -- construction --------------------------------------------------------------------
    @classmethod
    def from_items(cls, items: list[Item], timeline: str | None = None) -> TimeTable | None:
        times: list[float] = []
        for it in items:
            s = item_stamp(it)
            if s is None:
                return None
            times.append(s)
        return cls([it.key for it in items], times, timeline or DEFAULT_CLOCK) if items else None

    @classmethod
    def from_sidecar(cls, video: str | Path, timeline: str | None = None) -> TimeTable | None:
        side = load_sidecar(video)
        if not side:
            return None
        keys, times = [], []
        for i, f in enumerate(side["frames"]):
            s = stamp_seconds(f.get("stamp_ns", f.get("stamp")))
            if s is None:
                return None
            keys.append(str(f.get("key", f.get("frame", i))))
            times.append(s)
        return cls(keys, times, timeline or str(side.get("timeline") or DEFAULT_CLOCK))

    @classmethod
    def from_collection(cls, items: list[Item], timeline: str | None = None) -> TimeTable | None:
        """One video item with a sidecar: its frames are the clock; else the items'."""
        if len(items) == 1:
            video = video_part(items[0])
            if video is not None:
                return cls.from_sidecar(video, timeline)
        return cls.from_items(items, timeline)

    # -- lookups ---------------------------------------------------------------------------
    def nearest(self, t: float) -> str | None:
        if not self._order:
            return None
        i = bisect.bisect_left(self._order, (float(t), -1))
        cands = [j for j in (i - 1, i) if 0 <= j < len(self._order)]
        best = min(cands, key=lambda j: abs(self._order[j][0] - t))
        while best > 0 and self._order[best - 1][0] == self._order[best][0]:
            best -= 1  # twins sharing a stamp: the earliest item
        return self.keys[self._order[best][1]]

    def time_of(self, key: str) -> float | None:
        return self._by_key.get(key)

    def range(self) -> tuple[float, float] | None:
        return (self._order[0][0], self._order[-1][0]) if self._order else None


class GlobalClock:
    """The shared time base over the running canvas: the root view's latest time and a
    :class:`TimeTable` per view whose data has a clock. See the module docstring."""

    def __init__(self, live: Any) -> None:
        self.live = live
        self.now: TimeValue | None = None
        self._tables: dict[str, TimeTable] = {}

    @property
    def root(self) -> str | None:
        return self.live.data.get("root_view")

    # -- what views emit and receive --------------------------------------------------------
    def observe(self, view_id: str, value: TimeValue) -> bool:
        """A view emitted a time; the root view's moves the clock. True when it did."""
        if view_id != self.root:
            return False
        self.now = value
        return True

    def set_collection(self, view_id: str, items: list[Item], timeline: str | None) -> None:
        table = TimeTable.from_collection(items, timeline)
        if table is None:
            self._tables.pop(view_id, None)
        else:
            self._tables[view_id] = table

    def forget(self, view_id: str) -> None:
        self._tables.pop(view_id, None)

    def reset(self) -> None:
        self.now = None
        self._tables.clear()

    # -- lookups ----------------------------------------------------------------------------
    def table_of(self, view_id: str) -> TimeTable | None:
        return self._tables.get(view_id)

    def nearest(self, view_id: str, value: TimeValue | None = None) -> str | None:
        """The key of the view's item nearest ``value`` (default: ``now``); None when the
        view has no clock or answers another timeline."""
        value = self.now if value is None else value
        table = self._tables.get(view_id)
        if value is None or table is None:
            return None
        if value.timeline not in (table.timeline, OWN_TIMELINE):
            return None
        return table.nearest(value.t)

    def time_of(self, view_id: str, key: str) -> TimeValue | None:
        table = self._tables.get(view_id)
        t = table.time_of(key) if table is not None else None
        return None if t is None else TimeValue(t=t, timeline=table.timeline)  # type: ignore[union-attr]


__all__ = [
    "DEFAULT_CLOCK",
    "INDEX",
    "OWN_TIMELINE",
    "VIDEO_EXTS",
    "GlobalClock",
    "TimeTable",
    "TimeValue",
    "item_stamp",
    "load_sidecar",
    "stamp_seconds",
    "video_part",
]
