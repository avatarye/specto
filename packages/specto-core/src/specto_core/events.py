"""Event stream (HLD 3.5): a ring buffer with sequence numbers, one on the running
canvas (``engine.live.events``) and one for the process (``engine.app_events``: jobs).

``watch(since_seq)`` yields ``{seq, kind, payload}``. Kinds: ``doc.patched``,
``canvas.submitted|squashed|saved|save_failed``, ``history.restored``, ``job.progress|done|failed``,
``link.resolving|resolved|failed|arrivals``, ``view.health``. A ``since_seq`` older than
the buffer yields a ``gap`` event first; the client re-reads ``state``.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .clock import now_iso

EVENT_KINDS: tuple[str, ...] = (
    "gap",
    "doc.patched",
    "canvas.submitted",
    "canvas.squashed",
    "canvas.saved",
    "canvas.save_failed",
    "history.restored",
    "job.progress",
    "job.done",
    "job.failed",
    "link.resolving",
    "link.resolved",
    "link.failed",
    "link.arrivals",
    "view.health",
)


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int
    ts: str = Field(default_factory=now_iso)
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


Subscriber = Callable[[Event], None]


class EventStream:
    def __init__(self, capacity: int = 10_000) -> None:
        self._buf: deque[Event] = deque(maxlen=capacity)
        self._seq = 0
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def oldest_seq(self) -> int | None:
        return self._buf[0].seq if self._buf else None

    def emit(self, kind: str, payload: dict[str, Any] | None = None) -> Event:
        with self._lock:
            self._seq += 1
            ev = Event(seq=self._seq, kind=kind, payload=dict(payload or {}))
            self._buf.append(ev)
            subs = list(self._subs)
        for cb in subs:
            cb(ev)
        return ev

    def since(self, since_seq: int, *, limit: int | None = None) -> list[Event]:
        """Events with ``seq > since_seq``. If those are no longer buffered, the first
        returned event is ``gap`` with the oldest available seq."""
        with self._lock:
            events = [e for e in self._buf if e.seq > since_seq]
            oldest = self._buf[0].seq if self._buf else self._seq + 1
        out: list[Event] = []
        if since_seq + 1 < oldest and self._seq > since_seq:
            out.append(
                Event(
                    seq=since_seq,
                    kind="gap",
                    payload={
                        "oldest": oldest,
                        "requested": since_seq + 1,
                        "fix": "re-read state and watch from the gap's oldest",
                    },
                )
            )
        out.extend(events)
        if limit is not None:
            out = out[:limit]
        return out

    def subscribe(self, cb: Subscriber) -> Callable[[], None]:
        self._subs.append(cb)

        def unsubscribe() -> None:
            if cb in self._subs:
                self._subs.remove(cb)

        return unsubscribe

    def __len__(self) -> int:
        return len(self._buf)
