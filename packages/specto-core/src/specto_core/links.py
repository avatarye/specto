"""The link runtime (ADRs 0014, 0016): port values and their delivery along the links of
the running canvas document.

Views emit values on their output ports (``emit``); every link whose source is that port
resolves (parse, scan, group, look up) and the result lands on the target view's input
port, where the host receives it (``attach``). Resolution runs as a job on the engine's
``JobManager`` (worker threads or processes, never the GUI thread); while a resolve is
in flight new inputs for the same link coalesce, and only the latest value is resolved
once it lands. A link kind's resolver is a picklable module-level function registered
with ``register_resolver`` (the link entry it gets also carries ``target_type``, the
target view's type: an intaker feeding a ``3d`` view prepares scene buffers);
``specto_intake`` registers ``intaker`` on import. A kind may
declare what else it ``needs`` from the runtime: ``collections`` hands the resolver the
collections on the source and target views, which is how ``follow`` matches a selection
on one key field (``params.match``, e.g. ``frame``) across collections whose keys
differ. A two-way link resolves from whichever end emitted last (the resolver sees the
link oriented that way, with ``reversed`` set); a value delivered to a two-way port that
equals what that view itself last emitted is dropped (no echo). A result equal to what
the link already delivered (a collection with the same items and stat per part, the same
parsed contents) is not delivered again (#102): the target keeps its collection and
generation, so nothing downstream of it re-runs. A kind registered
``inline`` is a pure lookup (``time_map``: an offset between timelines) and runs on the
emitting thread without a job, so playback time flows at frame rate. ``python`` links
are the one kind resolved in two steps (``python_links``): the code runs once (after the
user approved its content hash) into a table, and a source value only looks that table
up; a per-pick one (``resolves_to: value``, #168) runs the code for each new source
value instead, debounced (``python_debounce_s``: a burst of picks runs only its last,
once ``poll`` sees the burst end) and dropping a result whose value is no longer the
source's.

Port values (one small model per port type that carries data):

- ``files``: ``FilesValue(root, paths)``, paths relative to ``root`` (POSIX); ``paths
  None`` means the whole folder: the project source's ``all_files`` is always that, so
  every link off it scans its own sources and follows arrivals with its own watch.
  The intaker from it into a file tree is never walked (#231): the tree gets a
  collection with the root alone and lists its folders itself, one level at a time,
  and follows the folders it loaded itself; ``project_files`` lists the whole folder
  only once some other whole-folder intaker off the project source resolved it.
- ``collection``: ``Collection(items, root, generation, link)``.
- ``selection``: ``Selection(keys, last)``.
- ``content``: ``ContentValue(root, entries)`` (``specto_core.contents``), one parsed
  entry per file; ``read_contents`` links produce it (resolver in ``specto_intake``).

Values that carry a ``link`` field are stamped with the id of the link that delivered
them, so hosts and the web item resolver can name the value by link.
- ``time``: ``TimeValue(t, timeline)`` (``timebase.py``).
- ``camera``: ``CameraValue(position, target, up, fov_y, near, far)`` (a 3D view's camera).

The runtime also keeps the **global clock** (``global_clock``, ``timebase.GlobalClock``): the
root view's latest time and a key <-> time table per view, built when a collection lands
on it (its intaker's ``params.timeline`` names the clock; default ``stamp``).

Watch mode (ADR 0016, story 57): a link kind may register a *watcher* beside its
resolver, ``fn(link, value, root, env) -> table`` (a cheap listing of what the link's
sources hold, ``{entry: stamp}``). For every link whose ``params.watch`` is true the
runtime runs the watcher on a daemon thread every ``watch_interval_s``; a changed table
is posted to the runtime, and ``poll()`` (the app's timer, or a test by hand) emits
``link.arrivals`` and resolves the link again once the folder has been quiet for
``watch_debounce_s``. The resolve is a job like any other; its result swaps in with the
next collection generation. Hosts show the target view as resolving meanwhile.

Nothing here is stored in the canvas document; the runtime rebuilds from it on load.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import weakref
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from . import fswatch
from .contents import ContentEntry, ContentValue
from .errors import NotFound
from .jobs import JobInfo, JobManager
from .kinds import Item
from .live_canvas import LiveCanvas
from .ports import PROJECT_ID, PROJECT_PORT
from .timebase import GlobalClock, TimeValue

log = logging.getLogger(__name__)

LINK_JOB_KIND = "link.resolve"
#: the project source's one output port, as an ``outputs`` key
PROJECT_OUT = (PROJECT_ID, PROJECT_PORT)
DEFAULT_WATCH_INTERVAL_S = 1.0
DEFAULT_WATCH_DEBOUNCE_S = 0.5
#: a per-pick Python link waits this long after a burst of picks before it runs the
#: latest (the first pick after a quiet spell runs at once)
DEFAULT_PYTHON_DEBOUNCE_S = 0.15


class FilesValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str
    paths: list[str] | None = Field(default=None, description="Relative POSIX paths; None: all.")
    stats: dict[str, list[int]] | None = Field(
        default=None,
        description="path -> [size, mtime_ns] as the sender already knew them (a file "
        "tree's picks, #76); a resolver uses these instead of stat'ing the files again.",
    )


class Collection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Item] = Field(default_factory=list)
    root: str = ""
    generation: int = 0
    link: str = ""


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[str] = Field(default_factory=list)
    last: str | None = None

    @property
    def picked(self) -> str | None:
        """The one item a view shows for this selection: the last picked, else the last
        of the keys, else None (what the gallery, image and json views agree on)."""
        return self.last or (self.keys[-1] if self.keys else None)


Vec3 = tuple[float, float, float]


class CameraValue(BaseModel):
    """A 3D view's camera: where it is, what it looks at, and its lens (fov in radians)."""

    model_config = ConfigDict(extra="forbid")
    position: Vec3
    target: Vec3
    up: Vec3 = (0.0, 1.0, 0.0)
    fov_y: float = Field(default=0.8, gt=0.0)
    near: float = Field(default=0.1, gt=0.0)
    far: float = Field(default=1000.0, gt=0.0)


#: ``fn(link, value, *, root, env, progress, **needs) -> output``; must be picklable
#: (module level). ``needs`` are the extras the kind registered for (see ``NEEDS``).
Resolver = Callable[..., Any]
RESOLVERS: dict[str, Resolver] = {}
#: What a kind's resolver takes besides the value: ``collections`` is
#: ``{"source": Collection | None, "target": Collection | None}`` of the two views.
RESOLVER_NEEDS: dict[str, tuple[str, ...]] = {}
NEEDS: tuple[str, ...] = ("collections",)
#: kinds whose resolver is a cheap lookup run inline on emit (no job)
INLINE_KINDS: set[str] = set()

#: ``receive(port, value)``; ``value`` is None when the link feeding the port went away.
Receiver = Callable[[str, Any], None]


#: ``fn(link, value, *, root, env) -> {entry: stamp}``: what the link's sources hold now;
#: runs on the watcher's thread, so it must be cheap (a directory listing, no decoding).
Watcher = Callable[..., dict[str, str]]
WATCHERS: dict[str, Watcher] = {}


class WatchListing(Protocol):
    """A watcher's table kept up to date from change notifications (SPEC-8): which
    folders to observe, a full listing, and how the named paths change the table,
    without listing the sources again."""

    def roots(self) -> list[tuple[str, bool]]:
        """``(folder, recursive)`` pairs to observe; raises ``NotifyUnavailable`` when a
        source cannot be observed (missing: ``retry`` is set)."""
        ...

    def full(self) -> dict[str, str]:
        """A full listing of the sources (the kind's watcher's table)."""
        ...

    def reset(self, table: dict[str, str]) -> None:
        """A full listing replaced the table: rebuild any index over it."""
        ...

    def apply(self, table: dict[str, str], paths: list[str]) -> tuple[int, int, int] | None:
        """Update ``table`` in place for the changed ``paths`` (absolute, native):
        ``(added, removed, changed)`` entries, or None when only a full listing can
        tell (a source root itself changed)."""
        ...

    def source_missing(self, table: dict[str, str]) -> bool:
        """The table says a source is not there (so notifications are worth trying
        again once it is)."""
        ...

    def note(self) -> str:
        """What the status adds while notifications are followed ('' for nothing): the
        parts of the sources only the periodic listing covers (folders reached through
        links, which send no notices)."""
        ...


class WatchMode(StrEnum):
    """How a watcher follows its sources: change notifications, or a listing every
    interval."""

    NOTIFY = "notify"
    POLL = "poll"


#: ``factory(link, value, *, root, env) -> WatchListing``: a watcher kind that can follow
#: change notifications instead of listing every interval
WATCH_LISTINGS: dict[str, Callable[..., WatchListing]] = {}
#: ``SPECTO_WATCH=poll`` makes every watcher list its sources every interval, as before
WATCH_ENV = "SPECTO_WATCH"
#: a watcher on notifications lists everything again this often anyway (a notice the
#: system dropped: a ReadDirectoryChangesW buffer overflow says nothing), and at least
#: ``RELIST_FACTOR`` listing durations apart, so a slow listing stays a small share. A
#: network drive more often, and never more than ``RELIST_REMOTE_MAX_S`` apart whatever
#: its listing takes: its server may not send every notice, or none (a NAS that accepts
#: the watch and stays silent)
RELIST_S = 30.0
RELIST_REMOTE_S = 10.0
RELIST_REMOTE_MAX_S = 30.0
RELIST_FACTOR = 50.0
#: notices arriving within this of each other are applied together (up to the cap)
COALESCE_S = 0.05
COALESCE_MAX_S = 0.5
#: this many changed paths in one batch is a storm (an agent writing a folder full):
#: its paths are applied as they come, and one full listing follows once it has been
#: quiet for ``SETTLE_S`` (an overflow inside it loses notices), never closer than
#: ``RELIST_FACTOR`` listing durations to the last; a storm that goes on is listed
#: after ``STORM_MIN_S``, then at doubling gaps up to ``STORM_MAX_S``
BURST_PATHS = 1000
SETTLE_S = 2.0
STORM_MIN_S = 5.0
STORM_MAX_S = 60.0
#: how often a watcher on notifications checks that they still come and that its
#: folders are still the ones armed
LIVENESS_S = 1.0
#: a watcher whose notifications stopped tries them again after this, doubling up to
#: ``RETRY_MAX_S``; notifications that held for ``RETRY_MAX_S`` reset the gap
RETRY_MIN_S = 30.0
RETRY_MAX_S = 300.0
#: safety-net listings in a row that found changes no notice reported: the drive sends
#: none, and the watcher polls
SILENT_MISSES = 2
NO_NOTICES = "this drive sends no change notices"


def register_resolver(
    kind: str, fn: Resolver, *, needs: tuple[str, ...] = (), inline: bool = False
) -> None:
    unknown = [n for n in needs if n not in NEEDS]
    if unknown:
        raise ValueError(f"unknown resolver needs {unknown}; known: {list(NEEDS)}")
    RESOLVERS[kind] = fn
    RESOLVER_NEEDS[kind] = tuple(needs)
    if inline:
        INLINE_KINDS.add(kind)
    else:
        INLINE_KINDS.discard(kind)


def register_watcher(
    kind: str, fn: Watcher, *, listing: Callable[..., WatchListing] | None = None
) -> None:
    """``fn`` lists the sources; ``listing``, when given, lets the watcher follow change
    notifications between listings (``WatchListing``)."""
    WATCHERS[kind] = fn
    if listing is not None:
        WATCH_LISTINGS[kind] = listing
    else:
        WATCH_LISTINGS.pop(kind, None)


#: ``fn(items, *, progress) -> items``: what a collection for a view of this type needs
#: once built (a 3d view's scene buffers); picklable, run in the resolve job. The
#: intaker prepares its own; a Python link's collection output gets it here (#176)
Preparer = Callable[..., Any]
PREPARERS: dict[str, Preparer] = {}


def register_preparer(view_types: tuple[str, ...] | list[str], fn: Preparer) -> None:
    for vt in view_types:
        PREPARERS[vt] = fn


#: every watcher thread alive in this process (tests sweep them between cases)
_LIVE_WATCHES: weakref.WeakSet[LinkWatch] = weakref.WeakSet()


def stop_all_watches(*, join: bool = True) -> int:
    """Stop every watcher thread in the process (a test harness sweep). Returns how
    many were running."""
    watches = [w for w in list(_LIVE_WATCHES) if w.running]
    for w in watches:
        w.stop(join=join)
    return len(watches)


class LinkWatch:
    """One watched link's watcher: a daemon thread that posts ``(link id, added,
    removed, changed)`` on ``changes`` when what the sources hold differs from the last
    time. The first listing is the baseline.

    With a ``listing`` (the kind's ``WatchListing`` factory) and change notifications
    available for its folders (``specto_core.fswatch``), the thread waits for notices
    and applies only the paths they name (``mode`` notify). It lists everything again:
    every ``RELIST_S`` or so (``RELIST_REMOTE_S`` to ``RELIST_REMOTE_MAX_S`` on a network
    drive); once a storm of notices has settled; when notices were lost or a source root
    itself changed; and when a watched folder was renamed or replaced (it is armed again
    on its path first). Safety-net listings that keep finding changes no notice reported
    mean the drive sends none: the watcher polls.

    Otherwise it lists the sources every ``interval_s`` (``mode`` poll) and ``reason``
    says why, in words for the status bar. A source that was missing is tried again
    after each listing; notifications that stopped, were refused or never came are tried
    again after ``RETRY_MIN_S``, doubling up to ``RETRY_MAX_S``. The thread holds its
    owner (the runtime) only weakly and ends with it, so a dropped engine never leaves a
    watcher behind; ``stop(join=True)`` ends it now."""

    def __init__(
        self,
        link_id: str,
        fn: Watcher,
        link: dict[str, Any],
        value: Any,
        *,
        root: str,
        env: dict[str, Any],
        changes: queue.SimpleQueue,
        interval_s: float,
        owner: object | None = None,
        listing: Callable[..., WatchListing] | None = None,
    ) -> None:
        self.link_id = link_id
        self.fn = fn
        self.link = link
        self.value = value
        self.root = root
        self.env = env
        self.changes = changes
        self.interval_s = max(0.01, float(interval_s))
        self.table: dict[str, str] | None = None
        #: full listings made (the baseline included), and notice batches applied
        self.polls = 0
        self.notices = 0
        #: (mode, reason), written as one pair (the runtime reads it from its thread)
        self._state: tuple[WatchMode, str] = (WatchMode.POLL, "")
        self._listing_factory = listing
        self._listing: WatchListing | None = None
        self._notifier: fswatch.Notifier | None = None
        self._remote = False
        # poll mode: when notifications are worth trying again
        self._retry_now = False  # a missing source: after each listing
        self._retry_at: float | None = None  # stopped, refused or silent: at this time
        self._retry_gap = RETRY_MIN_S
        self._notify_since = 0.0
        # notices handed over by watchdog's thread
        self._pending: set[str] = set()
        self._lost = False
        self._pending_lock = threading.Lock()
        self._wake = threading.Event()
        # full listings
        self._list_s = 0.0
        self._last_list = 0.0
        self._next_relist = 0.0
        self._dirty_since: float | None = None  # a storm or a loss wants a listing
        self._settle_at: float | None = None
        self._storm_gap = STORM_MIN_S
        self._silent_misses = 0
        self._last_error = ""
        self._owner = weakref.ref(owner) if owner is not None else None
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"specto-watch-{link_id}", daemon=True
        )
        _LIVE_WATCHES.add(self)

    @property
    def mode(self) -> WatchMode:
        return self._state[0]

    @property
    def reason(self) -> str:
        return self._state[1]

    @property
    def state(self) -> tuple[WatchMode, str]:
        """``(mode, reason)`` as one consistent pair."""
        return self._state

    def _set_state(self, mode: WatchMode, reason: str) -> None:
        self._state = (mode, reason)

    def start(self) -> LinkWatch:
        self._thread.start()
        return self

    def stop(self, *, join: bool = True, timeout: float = 5.0) -> None:
        """Ask the thread to end and (by default) wait for it; a listing in progress
        finishes first. Joining from the thread itself is skipped."""
        self._stop.set()
        self._wake.set()
        t = self._thread
        if join and t.is_alive() and t is not threading.current_thread():
            t.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread.is_alive() and not self._stop.is_set()

    # -- full listings -----------------------------------------------------------------
    def _check_now(self) -> bool:
        """One full listing and comparison. The watcher's own thread only (it owns the
        table). True when it changed."""
        t0 = time.monotonic()
        listing = self._listing
        if listing is not None:
            table = listing.full()
        else:
            table = self.fn(self.link, self.value, root=self.root, env=self.env)
        self.polls += 1
        self._list_s = time.monotonic() - t0
        before, self.table = self.table, table
        if listing is not None:
            listing.reset(table)
        if before is None:
            return False
        added = sum(1 for k in table if k not in before)
        removed = sum(1 for k in before if k not in table)
        changed = sum(1 for k, v in table.items() if k in before and before[k] != v)
        return self._post(added, removed, changed)

    def _post(self, added: int, removed: int, changed: int) -> bool:
        if not (added or removed or changed):
            return False
        self.changes.put((self.link_id, added, removed, changed))
        return True

    def _orphaned(self) -> bool:
        return self._owner is not None and self._owner() is None

    def _relist_gap(self) -> float:
        slow = RELIST_FACTOR * self._list_s
        if self._remote:
            return min(RELIST_REMOTE_MAX_S, max(RELIST_REMOTE_S, slow))
        return max(RELIST_S, slow)

    def _list(self, *, safety_net: bool = False) -> bool:
        """A full listing; True when it found changes. A failure is logged (once per
        message) and tried again at the next one."""
        changed = False
        try:
            changed = self._check_now()
            self._last_error = ""
        except Exception as e:  # a transient disk error: try again later
            msg = f"{type(e).__name__}: {e}"
            if msg != self._last_error:
                log.warning("watch %s: listing the sources failed: %s", self.link_id, msg)
                self._last_error = msg
        now = time.monotonic()
        self._last_list = now
        self._next_relist = now + self._relist_gap()
        self._dirty_since = self._settle_at = None
        if self._notifier is not None:
            self._set_state(WatchMode.NOTIFY, self._notify_note())
            if safety_net:
                # what the notices kept up to date was out of date: they missed it
                self._silent_misses = self._silent_misses + 1 if changed else 0
        return changed

    # -- the loop ------------------------------------------------------------------------
    def _run(self) -> None:
        # notifications first, so nothing between the baseline and them is lost; then
        # notices (or one listing per interval) until stopped or orphaned
        try:
            self._start_notify()
            self._list()
            while not self._stop.is_set() and not self._orphaned():
                if self._notifier is not None:
                    self._notify_step()
                    continue
                if self._stop.wait(self.interval_s):
                    break
                self._list()
                if self._retry_due() and self._start_notify():
                    self._list()  # what changed while notifications were starting
        finally:
            self._stop_notify()

    def _retry_due(self) -> bool:
        if self._retry_now:
            return True
        return self._retry_at is not None and time.monotonic() >= self._retry_at

    def _poll_from_now(self, reason: str, *, retry: bool) -> None:
        """Notifications stopped, were refused or never came: poll, and try them again
        after the retry gap (doubling each time they fail)."""
        self._stop_notify()
        self._set_state(WatchMode.POLL, f"polling: {reason}")
        self._retry_now = False
        self._retry_at = time.monotonic() + self._retry_gap if retry else None
        self._retry_gap = min(RETRY_MAX_S, self._retry_gap * 2)

    # -- notifications -------------------------------------------------------------------
    def _start_notify(self) -> bool:
        """Start notifications for the listing's folders; False (and ``reason``) when
        the watcher polls instead."""
        self._retry_now = False
        if self._listing_factory is None:
            self._set_state(WatchMode.POLL, "")
            self._retry_at = None
            return False
        if os.environ.get(WATCH_ENV, "").strip().lower() == "poll":
            self._set_state(WatchMode.POLL, f"polling: {WATCH_ENV}=poll")
            self._retry_at = None
            return False
        try:
            listing = self._listing_factory(self.link, self.value, root=self.root, env=self.env)
            roots = listing.roots()
            notifier = fswatch.Notifier(roots, self._on_paths, self._on_lost).start()
        except fswatch.NotifyUnavailable as e:
            self._set_state(WatchMode.POLL, f"polling: {e}")
            self._retry_now = e.retry
            # refused (inotify's watch limit, a share) is worth another try later; no
            # backend, a network file system or a missing source are not timed retries
            refused = str(e).startswith("change notifications refused")
            self._retry_at = time.monotonic() + self._retry_gap if refused else None
            if refused:
                self._retry_gap = min(RETRY_MAX_S, self._retry_gap * 2)
            return False
        except Exception as e:  # a listing that could not be made: poll as before
            self._set_state(WatchMode.POLL, f"polling: {e}")
            self._retry_at = None
            return False
        self._remote = any(fswatch.is_remote(folder) for folder, _ in roots)
        self._listing, self._notifier = listing, notifier
        if self.table is not None:
            listing.reset(self.table)
        self._retry_at = None
        self._notify_since = time.monotonic()
        self._silent_misses = 0
        self._set_state(WatchMode.NOTIFY, self._notify_note())
        return True

    def _notify_note(self) -> str:
        parts = []
        if self._remote:
            parts.append(f"network drive, relisted at least every {RELIST_REMOTE_MAX_S:.0f} s")
        note = self._listing.note() if self._listing is not None else ""
        if note:
            parts.append(note)
        return "; ".join(parts)

    def _stop_notify(self) -> None:
        n, self._notifier = self._notifier, None
        self._listing = None
        if n is not None:
            n.stop()

    def _on_paths(self, paths: list[str]) -> None:
        """watchdog's thread: remember the paths and wake the watcher."""
        with self._pending_lock:
            self._pending.update(paths)
        self._wake.set()

    def _on_lost(self) -> None:
        """watchdog's thread: notices were lost; the watcher lists everything."""
        with self._pending_lock:
            self._lost = True
        self._wake.set()

    def _notify_step(self) -> None:
        now = time.monotonic()
        due = min(self._next_relist, self._settle_at or self._next_relist)
        woke = self._wake.wait(max(0.0, min(LIVENESS_S, due - now)))
        if self._stop.is_set():
            return
        notifier = self._notifier
        assert notifier is not None
        if notifier.moved():
            # renamed, replaced or removed under its path: the handle follows the old
            # folder, so arm the path again and list it once
            self._stop_notify()
            if self._start_notify():
                self._list()
            else:
                self._list()
                self._retry_now = self._retry_now or self._missing()
            return
        if not notifier.alive:
            # a share that refuses notices, a source folder deleted: poll from here on
            listing = self._listing
            self._poll_from_now("change notifications stopped", retry=True)
            self._list()
            if (
                listing is not None
                and self.table is not None
                and listing.source_missing(self.table)
            ):
                self._retry_now = True  # followed again once it is back
            return
        if time.monotonic() - self._notify_since >= RETRY_MAX_S:
            self._retry_gap = RETRY_MIN_S  # notifications held: a later failure starts low
        if woke:
            self._take_notices()
        if self._notifier is None:
            return
        now = time.monotonic()
        if self._listing_due(now):
            if self._dirty_since is not None and (self._settle_at or 0) > now:
                self._storm_gap = min(STORM_MAX_S, self._storm_gap * 2)  # still storming
            else:
                self._storm_gap = STORM_MIN_S
            self._list()
        elif now >= self._next_relist:
            self._list(safety_net=True)
            if self._silent_misses >= SILENT_MISSES:
                self._silent_misses = 0
                self._poll_from_now(NO_NOTICES, retry=True)

    def _missing(self) -> bool:
        return any(v.startswith("missing:") for v in (self.table or {}).values())

    def _listing_due(self, now: float) -> bool:
        """A storm or a loss asked for a listing: once it has settled (or has gone on
        for the storm gap), and never closer than ``RELIST_FACTOR`` listing durations to
        the last listing."""
        if self._dirty_since is None:
            return False
        if now < self._last_list + RELIST_FACTOR * self._list_s:
            return False
        settled = self._settle_at is not None and now >= self._settle_at
        return settled or now - self._dirty_since >= self._storm_gap

    def _mark_dirty(self, now: float) -> None:
        if self._dirty_since is None:
            self._dirty_since = now
        self._settle_at = now + SETTLE_S

    def _take_notices(self) -> None:
        # let a burst of notices gather: until quiet for COALESCE_S, at most COALESCE_MAX_S
        start = time.monotonic()
        while True:
            self._wake.clear()
            if self._stop.wait(COALESCE_S):
                return
            if not self._wake.is_set() or time.monotonic() - start >= COALESCE_MAX_S:
                break
        with self._pending_lock:
            paths, self._pending = self._pending, set()
            lost, self._lost = self._lost, False
        now = time.monotonic()
        if lost:
            self._mark_dirty(now)
        if not paths:
            return
        if self._dirty_since is not None and self._settle_at is not None:
            self._settle_at = now + SETTLE_S  # the storm goes on: settle later
        if self.table is None or self._listing is None:
            self._mark_dirty(now)
            return
        if len(paths) >= BURST_PATHS:
            self._mark_dirty(now)  # a storm: one listing once it settles
            if len(paths) > max(BURST_PATHS, len(self.table) // 2):
                return  # more paths than half the table: that listing is cheaper
        try:
            counts = self._listing.apply(self.table, sorted(paths))
        except Exception as e:
            log.warning("watch %s: applying notices failed: %s", self.link_id, e)
            counts = None
        self.notices += 1
        if counts is None:
            self._mark_dirty(now)
            self._settle_at = now  # only a full listing can tell: as soon as allowed
        else:
            self._post(*counts)


def _field_of(item: Item, name: str) -> str | None:
    fields = item.meta.get("keys")
    value = fields.get(name) if isinstance(fields, dict) else None
    return None if value is None else str(value)


def resolve_follow(
    link: dict[str, Any],
    value: Any,
    *,
    root: str,
    env: dict,
    progress: Any = None,
    collections: dict[str, Any] | None = None,
) -> Any:
    """A selection follows by key, or on one key field (``params.match``): each selected
    key's field value in the source collection picks the target items with that value.
    Without ``match`` (or for any other port type) the value passes through."""
    match = (link.get("params") or {}).get("match")
    if match is None or not isinstance(value, Selection):
        return value
    cols = collections or {}
    source, target = cols.get("source"), cols.get("target")
    if not isinstance(source, Collection) or not isinstance(target, Collection):
        return Selection(keys=[], last=None)
    by_field: dict[str, list[str]] = {}
    for it in target.items:
        v = _field_of(it, match)
        if v is not None:
            by_field.setdefault(v, []).append(it.key)
    src_field = {it.key: _field_of(it, match) for it in source.items}
    keys: list[str] = []
    for k in value.keys:
        for tk in by_field.get(src_field.get(k) or "", []):
            if tk not in keys:
                keys.append(tk)
    hits = by_field.get(src_field.get(value.last) or "", []) if value.last else []
    return Selection(keys=keys, last=hits[0] if hits else None)


register_resolver("follow", resolve_follow, needs=("collections",))
#: python has no plain resolver (LinkRuntime._resolve_python); it rebuilds its table
#: from the source view's collection, so it re-resolves when that collection changes
RESOLVER_NEEDS["python"] = ("collections",)


def resolve_time_map(
    link: dict[str, Any], value: Any, *, root: str, env: dict, progress: Any = None
) -> Any:
    """``from_timeline`` -> ``to_timeline`` plus ``offset_ms`` (minus it from the far end
    of a two-way link). A time already on the destination timeline passes through; one
    on any other timeline is an error the agent can fix."""
    if not isinstance(value, TimeValue):
        return value
    p = link.get("params") or {}
    frm, to = str(p.get("from_timeline") or ""), str(p.get("to_timeline") or "")
    offset = float(p.get("offset_ms") or 0.0) / 1000.0
    if link.get("reversed"):
        frm, to, offset = to, frm, -offset
    if value.timeline == frm:
        return TimeValue(t=value.t + offset, timeline=to)
    if value.timeline == to:
        return value
    raise ValueError(
        f"the time arrived on timeline {value.timeline!r}, which this link does not map "
        f"({frm!r} -> {to!r}); set from_timeline to {value.timeline!r}"
    )


register_resolver("time_map", resolve_time_map, inline=True)


def _stop_watches_in(watches: dict[str, LinkWatch]) -> None:
    for w in list(watches.values()):
        w.stop(join=False)
    watches.clear()


def link_env(plugins: Any) -> dict[str, Any]:
    """What resolvers need from the plugin registry, as plain data (it crosses into
    worker processes): type aliases and the extension -> data kinds table."""
    return {"aliases": plugins.aliases(), "ext_kinds": plugins.ext_kinds()}


class LinkRuntime:
    def __init__(
        self,
        live: LiveCanvas,
        jobs: JobManager,
        *,
        root: Path | str | None = None,
        env: dict[str, Any] | None = None,
        trust: Any = None,
        watch_interval_s: float = DEFAULT_WATCH_INTERVAL_S,
        watch_debounce_s: float = DEFAULT_WATCH_DEBOUNCE_S,
        python_debounce_s: float = DEFAULT_PYTHON_DEBOUNCE_S,
        python_timeout_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        resolve: bool = True,
    ) -> None:
        from .python_links import RUN_TIMEOUT_S, TrustStore

        self.live = live
        self.jobs = jobs
        self.root = str(Path(root).resolve()) if root is not None else ""
        self.env = dict(env or {})
        #: the user's approvals of Python link files (ADR 0006)
        self.trust: TrustStore = trust if trust is not None else TrustStore.default()
        #: resolved Python tables: link id -> {key, data, resolves_to, output}
        self._tables: dict[str, dict[str, Any]] = {}
        self.events = live.events
        self.watch_interval_s = watch_interval_s
        self.watch_debounce_s = watch_debounce_s
        self.python_debounce_s = python_debounce_s
        #: a Python link's run longer than this fails (its process tree is killed)
        self.python_timeout_s = RUN_TIMEOUT_S if python_timeout_s is None else python_timeout_s
        self.clock = clock
        #: per-pick Python links waiting out their debounce: link id -> when to run
        self._due: dict[str, float] = {}
        #: when each per-pick link last ran off a pick (the leading edge of a burst)
        self._last_pick: dict[str, float] = {}
        self._watches: dict[str, LinkWatch] = {}
        self._changes: queue.SimpleQueue = queue.SimpleQueue()
        #: link id -> (time of the last change notice, added, removed, changed) not yet
        #: resolved: the debounce window
        self._arrivals: dict[str, tuple[float, int, int, int]] = {}
        #: link id -> (mode, reason) last said by ``link.watching`` (SPEC-8)
        self._watch_said: dict[str, tuple[str, str]] = {}
        #: latest value each output port emitted; the project source is always there
        self.outputs: dict[tuple[str, str], Any] = {PROJECT_OUT: FilesValue(root=self.root)}
        #: every file of the project folder as the project intaker last found them (None
        #: until it lands): the count on the project node, the prompt bar's folder summary
        self.project_files: FilesValue | None = None
        #: latest resolved value on each input port (what hosts receive)
        self.inputs: dict[tuple[str, str], Any] = {}
        self.receivers: dict[str, Receiver] = {}
        self._status: dict[str, dict[str, Any]] = {}
        self._inflight: dict[str, str] = {}  # link id -> job id
        self._pending: set[str] = set()  # links whose input changed while resolving
        self._fresh: set[str] = set()  # links refreshed: their next resolve stats afresh
        #: two-way links whose target end spoke last (resolve them target -> source)
        self._reversed: set[str] = set()
        self.global_clock = GlobalClock(live)
        self._generation = 0
        self._off = live.on_patch(self._on_patch)
        # the safety net behind close(): when the runtime is collected (even out of a
        # reference cycle) its watchers are stopped; the callback must not hold self
        self._finalizer = weakref.finalize(self, _stop_watches_in, self._watches)
        # ``resolve=False`` lets an owner publish the runtime first (``Engine.links``): an
        # inline resolve's events reach listeners that read ``engine.links`` back
        if resolve:
            self.resolve_all()

    def close(self) -> None:
        """Stop listening to the document and stop and join every watcher thread."""
        self._off()
        self._stop_watches(join=True)
        self._finalizer.detach()

    # -- reading -------------------------------------------------------------------------
    def input_of(self, view_id: str, port: str) -> Any:
        return self.inputs.get((view_id, port))

    def output_of(self, view_id: str, port: str) -> Any:
        return self.outputs.get((view_id, port))

    def status(self, link_id: str) -> dict[str, Any] | None:
        """``{state: waiting|resolving|resolved|failed|needs_approval, message}`` (and
        for a Python link ``path`` and ``hash``) or None for no link."""
        s = self._status.get(link_id)
        return dict(s) if s is not None else None

    def resolving(self, link_id: str) -> bool:
        return link_id in self._inflight

    def watching(self) -> list[str]:
        """Ids of the links with a live watcher."""
        return list(self._watches)

    def watch_status(self, link_id: str) -> dict[str, Any] | None:
        w = self._watches.get(link_id)
        if w is None:
            return None
        return {
            "running": w.running,
            "polls": w.polls,
            "interval_s": w.interval_s,
            "mode": w.state[0].value,
            "reason": w.state[1],
            "notices": w.notices,
        }

    def inputs_of(self, view_id: str) -> dict[str, Any]:
        return {p: v for (vid, p), v in self.inputs.items() if vid == view_id}

    # -- hosts -------------------------------------------------------------------------
    def attach(self, view_id: str, receive: Receiver) -> None:
        """A host takes the view's input ports; it gets the values already resolved."""
        self.receivers[view_id] = receive
        for port, value in self.inputs_of(view_id).items():
            receive(port, value)

    def detach(self, view_id: str) -> None:
        self.receivers.pop(view_id, None)

    def emit(self, view_id: str, port: str, value: Any) -> list[str]:
        """A view's output changed: resolve every link fed from it, and every two-way
        link whose other end it is (in reverse). Returns the link ids. Files that carry
        their stat and equal the last ones emitted resolve nothing: the same files,
        unchanged on disk (#102)."""
        if _same_files(self.outputs.get((view_id, port)), value):
            return []
        self.outputs[(view_id, port)] = value
        if isinstance(value, TimeValue):
            self.global_clock.observe(view_id, value)
        out = []
        for lid, link in self.live.data["links"].items():
            if _is_port(link["source"], view_id, port):
                self._reversed.discard(lid)
                if _per_pick(link):
                    self._debounce(lid)
                else:
                    self.resolve(lid)
                out.append(lid)
            elif link.get("two_way") and _is_port(link["target"], view_id, port):
                self._reversed.add(lid)
                self.resolve(lid)
                out.append(lid)
        return out

    def _debounce(self, link_id: str) -> None:
        """A new source value for a per-pick Python link: the first after a quiet spell
        runs now; one inside a burst (``python_debounce_s`` since the last) waits until
        the burst ends, and ``poll`` runs the latest value then."""
        now = self.clock()
        quiet = now - self._last_pick.get(link_id, float("-inf")) >= self.python_debounce_s
        if self.python_debounce_s <= 0 or (
            quiet and link_id not in self._due and link_id not in self._inflight
        ):
            self._last_pick[link_id] = now
            self.resolve(link_id)
            return
        self._due[link_id] = now + self.python_debounce_s

    # -- resolving ---------------------------------------------------------------------
    def resolve_all(self) -> None:
        for lid in list(self.live.data["links"]):
            self.resolve(lid)

    def refresh(self, view_id: str | None = None) -> list[str]:
        """The manual rescan (#102) for views whose auto-update is off: with no view (or
        the project source) every link off the project source scans its sources again
        (the file tree's intaker and every pinned one); with a view, the links feeding it
        files, which stat those files afresh rather than trusting the stat they came
        with. One rescan, not a document change (no undo step); only what changed is
        delivered. Returns the link ids rescanned."""
        links = self.live.data["links"]
        if view_id is None or view_id == PROJECT_ID:
            lids = [lid for lid, ln in links.items() if ln["source"]["view"] == PROJECT_ID]
        else:
            if view_id not in self.live.data["views"]:
                raise NotFound(
                    f"no view {view_id!r} on the canvas",
                    fix="omit view to refresh the whole project; canvas.state lists the views",
                )
            lids = [
                lid
                for lid, ln in links.items()
                if ln["target"]["view"] == view_id
                and isinstance(self.outputs.get(_ref(ln["source"])), FilesValue)
            ]
        for lid in lids:
            self._fresh.add(lid)
            self.resolve(lid)
        return lids

    def _oriented(self, link_id: str, link: dict[str, Any]) -> dict[str, Any]:
        """The link as the resolver sees it: source and target swapped when its target
        end spoke last (two-way)."""
        if link_id in self._reversed and link.get("two_way"):
            return {**link, "source": link["target"], "target": link["source"], "reversed": True}
        return link

    def resolve(self, link_id: str) -> None:
        link = self.live.data["links"].get(link_id)
        if link is None:
            return
        link = self._oriented(link_id, link)
        if link["kind"] == "python":
            if link_id in self._fresh:  # a refresh: the code runs again (it reads the disk)
                self._fresh.discard(link_id)
                self._tables.pop(link_id, None)
            self._resolve_python(link_id, link)
            return
        value = self.outputs.get((link["source"]["view"], link["source"]["port"]))
        if value is None:
            self._set_status(link_id, "waiting", "the source has produced nothing yet")
            self._stop_watch(link_id)
            return
        if self._tree_listing(link, value):
            self._fresh.discard(link_id)
            self._stop_watch(link_id)  # the tree follows the folders it loaded itself
            self._deliver_listing(link_id, link, value)
            return
        self._sync_watch(link_id, link, value)
        if link_id in self._inflight:
            self._pending.add(link_id)
            return
        resolver = RESOLVERS.get(link["kind"])
        if resolver is None:
            self._set_status(link_id, "failed", f"no resolver for link kind {link['kind']!r}")
            self.events.emit(
                "link.failed", {"link": link_id, "error": self._status[link_id]["message"]}
            )
            return
        if link["kind"] in INLINE_KINDS:
            self._resolve_inline(link_id, link, resolver, value)
            return
        if link_id in self._fresh:  # a refresh: the files are stat'ed again, not trusted
            self._fresh.discard(link_id)
            if isinstance(value, FilesValue) and value.stats is not None:
                value = value.model_copy(update={"stats": None})
        self._set_status(link_id, "resolving", "")
        self.events.emit("link.resolving", {"link": link_id, "target": dict(link["target"])})
        # the resolver sees the target view's type too (an intaker feeding a 3d view
        # prepares scene buffers; one feeding a file tree does not)
        target_view = self.live.data["views"].get(link["target"]["view"])
        job_link = dict(link)
        if target_view is not None:
            job_link["target_type"] = target_view["type"]
        # marked before the submit: a synchronous manager delivers (and pops) inside it
        self._inflight[link_id] = ""
        job = self.jobs.submit(
            LINK_JOB_KIND,
            resolver,
            job_link,
            value,
            root=self.root,
            env=self.env,
            canvas=link_id,
            on_done=lambda job, lid=link_id: self._on_done(lid, job),
            **self._needs_of(link),
        )
        if link_id in self._inflight:
            self._inflight[link_id] = job.id

    def _resolve_inline(
        self, link_id: str, link: dict[str, Any], resolver: Resolver, value: Any
    ) -> None:
        """A lookup kind: resolve here and now, on the emitting thread."""
        try:
            result = resolver(
                dict(link), value, root=self.root, env=self.env, **self._needs_of(link)
            )
        except Exception as e:
            first = f"{e}".splitlines()[0] or type(e).__name__
            self._set_status(link_id, "failed", first)
            self.events.emit(
                "link.failed", {"link": link_id, "target": dict(link["target"]), "error": first}
            )
            return
        self._set_status(link_id, "resolved", "")
        self._deliver(link_id, link, result)

    def _needs_of(self, link: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for need in RESOLVER_NEEDS.get(link["kind"], ()):
            if need == "collections":
                out[need] = {
                    "source": self.collection_of(link["source"]["view"]),
                    "target": self.collection_of(link["target"]["view"]),
                }
        return out

    def collection_of(self, view_id: str) -> Collection | None:
        """The collection on the view's input ports (its data), if one has resolved."""
        for (vid, _port), value in self.inputs.items():
            if vid == view_id and isinstance(value, Collection):
                return value
        return None

    def _on_done(self, link_id: str, job: JobInfo) -> None:
        self._inflight.pop(link_id, None)
        link = self.live.data["links"].get(link_id)
        if link is None:  # removed while resolving
            self._pending.discard(link_id)
            return
        link = self._oriented(link_id, link)
        if job.state == "done":
            self._set_status(link_id, "resolved", "")
            self._deliver(link_id, link, job.result)
        else:
            first = (job.error or job.state).splitlines()[0]
            self._set_status(link_id, "failed", first)
            self.events.emit(
                "link.failed", {"link": link_id, "target": dict(link["target"]), "error": first}
            )
        if link_id in self._pending:
            self._pending.discard(link_id)
            self.resolve(link_id)

    def _deliver(self, link_id: str, link: dict[str, Any], value: Any) -> None:
        target = link["target"]
        key = (target["view"], target["port"])
        if _unchanged(self.inputs.get(key), value, link_id):
            # a rescan that found what the view already has: nothing downstream runs
            self.events.emit(
                "link.resolved", {"link": link_id, "target": dict(target), "unchanged": True}
            )
            return
        if isinstance(value, Collection):
            self._generation += 1
            value = value.model_copy(update={"generation": self._generation, "link": link_id})
        elif isinstance(value, BaseModel) and "link" in type(value).model_fields:
            value = value.model_copy(update={"link": link_id})
        if link.get("two_way") and value == self.outputs.get(key):
            return  # an echo: the view already shows what it said itself
        self.inputs[key] = value
        # before the event: whoever hears it (the project node's tip) reads the new count
        whole = link["source"]["view"] == PROJECT_ID and link["kind"] == "intaker"
        if isinstance(value, Collection) and whole and _whole_folder(link):
            self._project_files(value)
        self.events.emit("link.resolved", {"link": link_id, "target": dict(target)})
        if isinstance(value, Collection):
            timeline = (link.get("params") or {}).get("timeline")
            self.global_clock.set_collection(target["view"], value.items, timeline)
        receive = self.receivers.get(target["view"])
        if receive is not None:
            receive(target["port"], value)
        if isinstance(value, Collection):
            self._collection_changed(target["view"])

    def _is_tree(self, view_id: str) -> bool:
        view = self.live.data["views"].get(view_id)
        return view is not None and view.get("type") == "file_tree"

    def _tree_listing(self, link: dict[str, Any], value: Any) -> bool:
        """An intaker handing a file tree the whole folder (``project.all_files``, #231):
        never walked. The tree lists its folders itself, one level at a time, over the
        link's sources and screener, and resolves a folder pick to its files when it is
        picked; a tree fed explicit paths (another tree's picks) resolves as any view."""
        return (
            link["kind"] == "intaker"
            and self._is_tree(link["target"]["view"])
            and isinstance(value, FilesValue)
            and value.paths is None
        )

    def _deliver_listing(self, link_id: str, link: dict[str, Any], value: FilesValue) -> None:
        """Hand the tree a collection with the root alone, every time the link resolves
        (a refresh, a changed link: the tree lists its loaded folders again)."""
        target = link["target"]
        self._generation += 1
        col = Collection(root=value.root or self.root, generation=self._generation, link=link_id)
        self._set_status(link_id, "resolved", "")
        self.inputs[_ref(target)] = col
        self.events.emit(
            "link.resolved", {"link": link_id, "target": dict(target), "listing": True}
        )
        receive = self.receivers.get(target["view"])
        if receive is not None:
            receive(target["port"], col)

    def _project_files(self, col: Collection) -> None:
        """A whole-folder intaker off ``project.all_files`` landed: ``project_files`` now
        lists every file it found (what the project node's hover and the prompt bar's
        folder summary read). None until one does: the file tree's own intaker is not
        walked (#231). The output port itself keeps meaning the whole folder: every link
        off it scans its own sources, at once, and follows arrivals only with its own
        ``params.watch`` (#48)."""
        paths: list[str] = []
        for it in col.items:
            rels = it.meta.get("rel")
            paths.extend(rels.values() if isinstance(rels, dict) and rels else it.parts.values())
        self.project_files = FilesValue(root=col.root or self.root, paths=sorted(set(paths)))

    def _collection_changed(self, view_id: str) -> None:
        """A view's data changed: the links that match against it resolve again."""
        for lid, link in self.live.data["links"].items():
            if "collections" not in RESOLVER_NEEDS.get(link["kind"], ()):
                continue
            if view_id in (link["source"]["view"], link["target"]["view"]):
                self.resolve(lid)

    def _set_status(self, link_id: str, state: str, message: str, **extra: Any) -> None:
        self._status[link_id] = {"state": state, "message": message, **extra}

    # -- python links (python_links.py) -------------------------------------------------
    def _resolve_python(self, link_id: str, link: dict[str, Any]) -> None:
        """Step one: the approved code runs once into a table (a job, out of process);
        step two: the latest source value looks the table up. The table is rebuilt when
        the file content, the params or the source's collection change. A per-pick link
        (``resolves_to: value``) has the source value in that key too: the code runs for
        each new value, and only the latest value's result lands (``_python_done``)."""
        from . import python_links as pl

        py = link["python"]
        target = dict(link["target"])
        if py["resolves_to"] == "value":
            self._resolve_python_value(link_id, link)
            return
        sha = pl.code_hash(self.root, py["path"])
        if sha is None:
            self._tables.pop(link_id, None)
            self._fail(link_id, target, f"{py['path']} is missing; write it with def {py['entry']}")
            return
        if not self._python_approved(link_id, link, sha):
            return
        source = self._script_items(link)
        params = json.dumps(link.get("params") or {}, sort_keys=True)
        key = [sha, params, source.generation if source is not None else 0]
        table = self._tables.get(link_id)
        if table is not None and table["key"] == key:
            self._python_lookup(link_id, link, table)
            return
        if link_id in self._inflight:
            self._pending.add(link_id)
            return
        items = [it.model_dump() for it in source.items] if source is not None else []
        self._submit_python(link_id, link, key, pl.build_python_table, sha, items)

    def _script_items(self, link: dict[str, Any]) -> Collection | None:
        """The collection a Python link's script gets as ``items``: the source view's,
        except a file tree's, whose data is the listing of the whole project (handed
        over on every pick it would cost a serialisation of every file; the script has
        ``root``)."""
        vid = link["source"]["view"]
        return None if self._is_tree(vid) else self.collection_of(vid)

    def _python_approved(self, link_id: str, link: dict[str, Any], sha: str) -> bool:
        """True when the user approved this content; else the link needs approval (it
        says so) and its old results go."""
        py = link["python"]
        if self.trust.approved(sha):
            return True
        self._tables.pop(link_id, None)
        self._set_status(
            link_id,
            "needs_approval",
            f"{py['path']} needs your approval before it runs",
            path=py["path"],
            hash=sha,
        )
        self.events.emit(
            "link.needs_approval",
            {"link": link_id, "target": dict(link["target"]), "path": py["path"], "hash": sha},
        )
        return False

    def _python_value_key(self, link: dict[str, Any], sha: str) -> list[Any] | None:
        """A per-pick link's resolve key: the code, the params, the source collection
        and the source's current value (None: the source has said nothing yet)."""
        from . import python_links as pl

        value = self.outputs.get((link["source"]["view"], link["source"]["port"]))
        if value is None:
            return None
        source = self._script_items(link)
        return [
            sha,
            json.dumps(link.get("params") or {}, sort_keys=True),
            source.generation if source is not None else 0,
            json.dumps(pl.value_json(value), sort_keys=True, default=str),
        ]

    def _resolve_python_value(self, link_id: str, link: dict[str, Any]) -> None:
        """A per-pick link: the approved code runs for the source's current value (a
        job, out of process); the same key again delivers what that run returned."""
        from . import python_links as pl

        py = link["python"]
        sha = pl.code_hash(self.root, py["path"])
        if sha is None:
            self._tables.pop(link_id, None)
            self._fail(
                link_id,
                dict(link["target"]),
                f"{py['path']} is missing; write it with "
                f"def {py['entry']}(value, items, params, root)",
            )
            return
        if not self._python_approved(link_id, link, sha):
            return
        key = self._python_value_key(link, sha)
        if key is None:
            extra = {"path": py["path"], "hash": sha}
            self._set_status(link_id, "waiting", "the source has produced nothing yet", **extra)
            return
        table = self._tables.get(link_id)
        if table is not None and table["key"] == key:
            self._python_lookup(link_id, link, table)
            return
        if link_id in self._inflight:
            self._pending.add(link_id)
            return
        source = self._script_items(link)
        items = [it.model_dump() for it in source.items] if source is not None else []
        value = self.outputs[(link["source"]["view"], link["source"]["port"])]
        self._submit_python(
            link_id, link, key, pl.build_python_value, sha, pl.value_json(value), items
        )

    def _submit_python(
        self, link_id: str, link: dict[str, Any], key: list[Any], fn: Any, sha: str, *args: Any
    ) -> None:
        py = link["python"]
        self._set_status(link_id, "resolving", f"running {py['path']}", path=py["path"], hash=sha)
        self.events.emit("link.resolving", {"link": link_id, "target": dict(link["target"])})
        # the job sees the target view's type (a content output for a map must be GeoJSON)
        job_link = dict(link)
        target_view = self.live.data["views"].get(link["target"]["view"])
        if target_view is not None:
            job_link["target_type"] = target_view["type"]
        prepare = (
            PREPARERS.get(job_link.get("target_type") or "")
            if py["output"] == "collection"
            else None
        )
        self._inflight[link_id] = ""
        job = self.jobs.submit(
            LINK_JOB_KIND,
            fn,
            job_link,
            sha,
            *args,
            root=self.root,
            prepare=prepare,
            timeout=self.python_timeout_s,
            canvas=link_id,
            on_done=lambda job, lid=link_id, k=key: self._python_done(lid, k, job),
        )
        if link_id in self._inflight:
            self._inflight[link_id] = job.id

    def _python_done(self, link_id: str, key: list[Any], job: JobInfo) -> None:
        from .python_links import ScriptFailed

        self._inflight.pop(link_id, None)
        link = self.live.data["links"].get(link_id)
        if link is None:
            self._pending.discard(link_id)
            return
        py = link["python"]
        stale = py["resolves_to"] == "value" and key != self._python_value_key(link, key[0])
        if stale:
            # the source moved on while this ran: its result never lands; the latest
            # value runs next (now, or when its debounce window closes)
            if link_id not in self._due:
                self._pending.add(link_id)
        elif job.state == "done" and isinstance(job.result, ScriptFailed):
            # what the code got wrong, with its own traceback for the agent; kept under
            # its key, so new data on either end does not run the same failure again
            self._tables[link_id] = {"key": key, "failed": job.result}
            self._fail(link_id, dict(link["target"]), job.result.message, job.result.trace)
        elif job.state == "done":
            table = {
                "key": key,
                "data": job.result,
                "resolves_to": py["resolves_to"],
                "output": py["output"],
            }
            self._tables[link_id] = table
            self.events.emit("link.resolved", {"link": link_id, "target": dict(link["target"])})
            self._python_lookup(link_id, link, table)
        else:
            first = (job.error or job.state).splitlines()[0]
            self._fail(link_id, dict(link["target"]), first)
        if link_id in self._pending:
            self._pending.discard(link_id)
            self.resolve(link_id)

    def _python_lookup(self, link_id: str, link: dict[str, Any], table: dict[str, Any]) -> None:
        from . import python_links as pl

        failed = table.get("failed")
        if failed is not None:  # this key failed; it stays failed until the key changes
            if (self._status.get(link_id) or {}).get("state") != "failed":
                self._fail(link_id, dict(link["target"]), failed.message, failed.trace)
            return

        value = self.outputs.get((link["source"]["view"], link["source"]["port"]))
        extra = {"path": link["python"]["path"], "hash": table["key"][0]}
        if value is None:
            self._set_status(link_id, "waiting", "the source has produced nothing yet", **extra)
            return
        if table["resolves_to"] == "value":
            found, out = table["data"] is not None, table["data"]
        else:
            found, out = pl.lookup(table["resolves_to"], table["data"], value)
        if not found:
            py = link["python"]
            message = (
                f"{py['path']}:{py['entry']} returned None: nothing delivered"
                if table["resolves_to"] == "value"
                else "no entry for the current source value"
            )
            self._set_status(link_id, "resolved", message, **extra)
            return
        self._set_status(link_id, "resolved", "", **extra)
        self._deliver(link_id, link, out)

    def _fail(self, link_id: str, target: dict[str, Any], message: str, trace: str = "") -> None:
        """The link failed; a Python link's own traceback (``trace``) goes on its status
        and the event, where the agent reads it."""
        extra = {"trace": trace} if trace else {}
        self._set_status(link_id, "failed", message, **extra)
        self.events.emit(
            "link.failed", {"link": link_id, "target": target, "error": message, **extra}
        )

    def approval_request(self, link_id: str) -> dict[str, Any]:
        """What approving a python link means right now: ``{link, path, hash, target,
        approved}``, the file's current content hash and whether the user approved that
        content already. ``NotFound`` for a link that is not python or a missing file."""
        from . import python_links as pl

        link = self.live.data["links"].get(link_id)
        if link is None or link["kind"] != "python":
            raise NotFound(f"{link_id!r} is not a python link", fix="canvas.state lists the links")
        py = link["python"]
        sha = pl.code_hash(self.root, py["path"])
        if sha is None:
            raise NotFound(f"{py['path']} is missing", fix=f"write it with def {py['entry']}")
        return {
            "link": link_id,
            "path": py["path"],
            "hash": sha,
            "target": dict(link["target"]),
            "approved": self.trust.approved(sha),
        }

    def approve(self, link_id: str) -> dict[str, Any]:
        """The user approves the Python file a link references, as it is right now; the
        link resolves. Returns ``{link, path, hash}``. This is the user's gesture (the
        view's Approve button, the approval card): no command reaches it (#203)."""
        req = self.approval_request(link_id)
        self.trust.approve(req["hash"], path=req["path"])
        self.resolve(link_id)
        return {"link": link_id, "path": req["path"], "hash": req["hash"]}

    # -- watch ---------------------------------------------------------------------------
    def _sync_watch(self, link_id: str, link: dict[str, Any], value: Any) -> None:
        """A watcher runs exactly while the link says ``watch`` and its kind has one;
        a changed link or input restarts it over the new sources."""
        fn = WATCHERS.get(link["kind"])
        # no project folder (headless, in memory): nothing on disk to follow
        wanted = (
            fn is not None and bool(self.root) and bool((link.get("params") or {}).get("watch"))
        )
        w = self._watches.get(link_id)
        if w is not None and (not wanted or w.link != link or w.value != value):
            self._stop_watch(link_id)
            w = None
        if wanted and w is None:
            assert fn is not None
            self._watches[link_id] = LinkWatch(
                link_id,
                fn,
                dict(link),
                value,
                root=self.root,
                env=self.env,
                changes=self._changes,
                interval_s=self.watch_interval_s,
                owner=self,
                listing=WATCH_LISTINGS.get(link["kind"]),
            ).start()

    def _stop_watch(self, link_id: str, *, join: bool = False) -> None:
        w = self._watches.pop(link_id, None)
        if w is not None:
            w.stop(join=join)
        self._arrivals.pop(link_id, None)
        self._watch_said.pop(link_id, None)

    def _say_watch_modes(self) -> None:
        """``link.watching {link, target, mode, reason}`` when a watcher settles on how
        it follows its sources, and again when that changes (notifications stopped, a
        missing source came back): the status bar says when a view is polled."""
        for lid, w in list(self._watches.items()):
            if w.polls == 0:
                continue  # not started yet: the mode is decided before the baseline
            mode, reason = w.state
            said = (mode.value, reason)
            if self._watch_said.get(lid) == said:
                continue
            self._watch_said[lid] = said
            link = self.live.data["links"].get(lid)
            if link is None:
                continue
            self.events.emit(
                "link.watching",
                {"link": lid, "target": dict(link["target"]), "mode": said[0], "reason": said[1]},
            )

    def watch_note(self, view_id: str) -> str:
        """What the status bar adds to "auto-update on" for a view (SPEC-8): why a
        watcher feeding it polls, or how it follows a network drive; '' when its
        watchers all follow change notifications."""
        notes = []
        for lid, w in self._watches.items():
            link = self.live.data["links"].get(lid)
            if link is None or link["target"]["view"] != view_id or w.polls == 0:
                continue
            reason = w.state[1]
            if reason and reason not in notes:
                notes.append(reason)
        return "; ".join(notes)

    def _stop_watches(self, *, join: bool = False) -> None:
        for lid in list(self._watches):
            self._stop_watch(lid, join=join)

    def poll(self) -> list[str]:
        """Take the watchers' change notices and, for every link quiet for
        ``watch_debounce_s``, emit ``link.arrivals`` and resolve it again. Returns the
        ids resolved. The app calls this from its timer, headless code by hand. Per-pick
        Python links whose debounce window closed run their latest value here too."""
        now = self.clock()
        out: list[str] = []
        for lid, when in list(self._due.items()):
            if now < when:
                continue
            del self._due[lid]
            self._last_pick[lid] = now
            self.resolve(lid)
            out.append(lid)
        while True:
            try:
                lid, added, removed, changed = self._changes.get_nowait()
            except queue.Empty:
                break
            if lid not in self._watches:
                continue  # a notice from a watcher already stopped
            _, a, r, c = self._arrivals.get(lid, (now, 0, 0, 0))
            self._arrivals[lid] = (now, a + added, r + removed, c + changed)
        self._say_watch_modes()
        for lid, (since, added, removed, changed) in list(self._arrivals.items()):
            if now - since < self.watch_debounce_s:
                continue
            del self._arrivals[lid]
            link = self.live.data["links"].get(lid)
            if link is None:
                continue
            self.events.emit(
                "link.arrivals",
                {
                    "link": lid,
                    "target": dict(link["target"]),
                    "added": added,
                    "removed": removed,
                    "changed": changed,
                },
            )
            self.resolve(lid)
            out.append(lid)
        return out

    # -- the document changed ------------------------------------------------------------
    def _on_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        if meta.get("kind") == "load":
            self.inputs.clear()
            self._status.clear()
            self._pending.clear()
            self._fresh.clear()
            self.global_clock.reset()
            self._tables.clear()
            self._due.clear()
            self._last_pick.clear()
            self._stop_watches()
            self.outputs = {PROJECT_OUT: FilesValue(root=self.root)}
            self.project_files = None
            self.resolve_all()
            return
        links = self.live.data["links"]
        views = self.live.data["views"]
        touched: dict[str, None] = {}
        for op in patch:
            parts = op.get("path", "").split("/")
            if parts[1:] == ["root_view"]:
                self.global_clock.now = None  # another player's timeline from now on
            if len(parts) < 3:
                continue
            section, oid = parts[1], parts[2]
            if section == "links":
                touched[oid] = None
            elif section == "views" and oid not in views:
                self._forget_view(oid)
        for lid in touched:
            if lid in links:
                self.resolve(lid)
            else:
                self._unlink(lid)

    def _unlink(self, link_id: str) -> None:
        """A link went away: its target port is unfed again."""
        self._status.pop(link_id, None)
        self._pending.discard(link_id)
        self._fresh.discard(link_id)
        self._stop_watch(link_id)
        self._reversed.discard(link_id)
        self._tables.pop(link_id, None)
        self._due.pop(link_id, None)
        self._last_pick.pop(link_id, None)
        for key in [k for k, v in self.inputs.items() if getattr(v, "link", None) == link_id]:
            self._clear_input(key)
        # inputs without a link tag (follow values): clear the ports no link feeds any more
        fed = {
            (ln["target"]["view"], ln["target"]["port"]) for ln in self.live.data["links"].values()
        }
        for key in [k for k in self.inputs if k not in fed]:
            self._clear_input(key)

    def _clear_input(self, key: tuple[str, str]) -> None:
        if isinstance(self.inputs.pop(key, None), Collection):
            self.global_clock.forget(key[0])
        receive = self.receivers.get(key[0])
        if receive is not None:
            receive(key[1], None)

    def _forget_view(self, view_id: str) -> None:
        self.receivers.pop(view_id, None)
        self.global_clock.forget(view_id)
        for key in [k for k in self.outputs if k[0] == view_id]:
            del self.outputs[key]
        for key in [k for k in self.inputs if k[0] == view_id]:
            del self.inputs[key]


def _parts_signature(col: Collection) -> list[tuple[str, dict[str, Any], Any, Any]]:
    """What makes two collections the same data: the items in order, by key, with their
    key fields and each part's path and stat (``meta.stat[role] = [size, mtime_ns]``)."""
    return [(it.key, it.parts, it.meta.get("stat"), it.meta.get("keys")) for it in col.items]


def _whole_folder(link: dict[str, Any]) -> bool:
    """An intaker that takes the whole project folder (sources absent or ``["."]``, no
    screener): only its listing is the project's. A tree narrowed to a subfolder or to
    some kinds lists a part, and must not stand in for the project (#169: the count
    jumped with whichever tree resolved last)."""
    params = link.get("params") or {}
    return list(params.get("sources") or ["."]) == ["."] and not params.get("screener")


def _unchanged(current: Any, value: Any, link_id: str) -> bool:
    """True when ``value`` (a fresh result of ``link_id``) carries the same data as what
    that link already delivered: a collection with the same items and stat per part, or
    equal parsed contents. Other port values always deliver."""
    if current is None or getattr(current, "link", None) != link_id:
        return False
    if isinstance(value, Collection) and isinstance(current, Collection):
        return value.root == current.root and _parts_signature(value) == _parts_signature(current)
    if isinstance(value, ContentValue) and isinstance(current, ContentValue):
        return value.model_copy(update={"link": link_id}) == current
    return False


def _same_files(before: Any, value: Any) -> bool:
    """``value`` names the files ``before`` did, each with the same stat: nothing on disk
    changed for whoever reads them (a file tree emitting its picks again)."""
    if not isinstance(value, FilesValue) or value.paths is None or value.stats is None:
        return False
    return all(p in value.stats for p in value.paths) and value == before


def _per_pick(link: dict[str, Any]) -> bool:
    return link["kind"] == "python" and link["python"]["resolves_to"] == "value"


def _ref(ref: dict[str, Any]) -> tuple[str, str]:
    return ref["view"], ref["port"]


def _is_port(ref: dict[str, Any], view_id: str, port: str) -> bool:
    return ref["view"] == view_id and ref["port"] == port


__all__ = [
    "DEFAULT_PYTHON_DEBOUNCE_S",
    "DEFAULT_WATCH_DEBOUNCE_S",
    "DEFAULT_WATCH_INTERVAL_S",
    "INLINE_KINDS",
    "LINK_JOB_KIND",
    "NEEDS",
    "PREPARERS",
    "RESOLVERS",
    "RESOLVER_NEEDS",
    "WATCHERS",
    "CameraValue",
    "Collection",
    "ContentEntry",
    "ContentValue",
    "FilesValue",
    "GlobalClock",
    "LinkRuntime",
    "LinkWatch",
    "Preparer",
    "Receiver",
    "Resolver",
    "Selection",
    "TimeValue",
    "WatchListing",
    "WatchMode",
    "Watcher",
    "link_env",
    "register_preparer",
    "register_resolver",
    "register_watcher",
    "resolve_follow",
    "resolve_time_map",
    "stop_all_watches",
]
