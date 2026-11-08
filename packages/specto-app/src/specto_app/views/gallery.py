"""The ``gallery`` view host (contracts section 8; HLD 3.7, 4.1, 4.2; ports per ADR 0014).

One :class:`GalleryHost` per live gallery view. It owns a ``GalleryView.qml`` item whose
heart is the native ``GalleryItem`` (``Specto.Native``, C++): the item lays out, scrolls,
zooms (thumbnail size, columns reflowed to the width, anchored under the cursor), draws
tiles and their captions (on the thumbnail's bottom edge, SPEC-6); Python never touches
a pixel. The host:

- receives its collection on the ``data`` input port (what the gallery's intaker link
  resolved) and tells the item the count and the captions (key, file name);
- feeds thumbnails: the item says which indices it wants and at which tier
  (``firstWanted``/``lastWanted``/``wantTier`` plus ``missing()``), the host batches
  jobs on the worker pool at that tier's size, and each result (an RGBA buffer in
  shared memory) is handed to the item by shm name and offset. Images are decoded;
  every other kind gets its thumbnail from ``specto_app.thumbs`` (a video's poster
  frame, a pdf's first page, an excerpt card for text, a kind icon for 3D), cached per
  file and stat in memory and under the project's ``.specto/cache``. An item with
  several parts shows its first part in ``thumbs.KIND_ORDER`` (image before video
  before pdf...), so a frame with a json sidecar still shows the frame;
- emits ``selection`` (the selected keys and the last one picked) on click and keyboard
  steps, and shows a ``selection`` arriving on its input port without re-emitting it;
- emits ``time`` with a click when the collection has a clock (the item's stamp, looked
  up in the global clock's table of this view; the clicked frame becomes the current
  item), and shows a ``time`` arriving on its input port as the nearest frame, the
  **current item**: marked apart from the selection ring and revealed, never selected
  and never emitted (following is not selecting), so a time-mapped gallery follows the
  root view's playback while the selection stays the user's pick;
- answers ``snapshot()`` with the visible keys, selection, current item, view and
  decode health.

Interaction map (what the native item does with input):

| input | effect |
|---|---|
| wheel | scroll (eased); touchpad pixel deltas scroll directly |
| Ctrl+wheel, pinch | thumbnail size: one column per notch, the tile under the cursor stays |
| slot resize | same thumbnail size, columns reflow to fill; the top-left item stays |
| drag on a tile | drag the tile out (#185: its file, as the drag contract's mime data) |
| drag on the gaps, touch | pan with inertia on release |
| click | select (right-click: the shared context menu, #154) |
| arrows, PgUp/PgDn, Home/End | move the selection (and reveal it) |
| Space | quick look of the selection |
| + / - | zoom in / out around the selection (or the centre) |
| hover 400 ms | preview popup near the cursor (QML, decoded by Qt's image loader) |

| Enter, double-click | select and open the gallery's preview (every kind, #115) |
| context menu | Open preview, Open image, Quick look; Copy path, Copy relative path |
| sort pills (footer) | reorder by key, name or time; the active one again flips the direction |

The thumbnail size is the view's own ``params.tile`` (px; ``params.columns`` sets it as a
column count when there is no tile): a zoom gesture remembers it once it ends (navigation,
like ``params.selected``: no undo step), so it survives clicks and a relaunch, and the agent
sets it by writing the param. A patch re-applies only the params it touches (#173).

The sort is the view's own ``params.sort`` (``key`` / ``name`` / ``time``, ``-`` prefix
for descending; empty: the collection's order), written as a user submit so the agent
sees it. Opening the preview is ``specto_core.gestures.open_gallery_preview``, the image
view (the context menu's Open image; the agent composes it too) ``open_image_view``; a tile that is
not an image has no hover popup (its thumbnail is already the best picture of it) and
its quick look opens the preview. The item is reached through a small adapter
(:class:`NativeGallery`) so the host logic runs headless against :class:`FakeGallery`
doubles in tests and benchmarks.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from PySide6.QtCore import QByteArray, QCoreApplication, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtQuick import QQuickItem

from specto_app import thumbs
from specto_app.drag import DragPayload, payload_for
from specto_app.hosts.base import HostContext, QuickViewHost
from specto_app.menu import MenuCall, MenuEntry, MenuItemRef, MenuTarget, default_registry
from specto_core.containers import is_virtual
from specto_core.errors import SpectoError
from specto_core.gestures import open_gallery_preview, open_image_view
from specto_core.jobs import JobInfo, JobManager
from specto_core.kinds import DataRef, DecodeRequest, Item, PluginManifest, ViewManifest
from specto_core.links import Collection, Selection, TimeValue, arrival_entry

from . import gallery_native

THUMB_PX = 176  # GalleryItem full tier: 192 px atlas cell minus 2 x 8 px gutter
LARGE_PX = 384  # GalleryItem large tier: 400 px cell minus gutters
TIER_MINI, TIER_FULL, TIER_LARGE = 0, 1, 2
DEFAULT_BATCH = 16
SNAPSHOT_KEYS_CAP = 256
TILE_EPS = 0.5  # px: a remembered size within this of the shown one is the same size
DATA, SELECTION, TIME = "data", "selection", "time"
#: arrival entries kept pending (not delivered yet) at most, and how many deliveries
#: an entry waits for before it is forgotten (SPEC-59)
PENDING_CAP = 1000
PENDING_DELIVERIES = 2


class NewPill(NamedTuple):
    """The "+N new" pill: how many arrivals are not seen yet, the index of the first
    (-1 for none)."""

    count: int
    first: int


NO_NEW = NewPill(0, -1)
SORTS: tuple[str, ...] = ("key", "name", "time")

GALLERY_PLUGIN = PluginManifest(name="app-gallery", version="0.2.0", views=[])


class GalleryItemLike(Protocol):
    """What the host needs from the native item (or a test double)."""

    def set_count(self, n: int) -> None: ...
    def count(self) -> int: ...
    def wanted(self) -> tuple[int, int, int]: ...
    def visible(self) -> tuple[int, int]: ...
    def missing(self, first: int, last: int, tier: int) -> list[int]: ...
    def push_shm(self, index: int, shm: str, offset: int, w: int, h: int) -> bool: ...
    def push_bytes(self, index: int, rgba: bytes, w: int, h: int) -> bool: ...
    def set_failed(self, index: int, failed: bool = True) -> None: ...
    def clear(self) -> None: ...
    def select(self, index: int) -> None: ...
    def selected(self) -> int: ...
    def set_current(self, index: int) -> None: ...
    def current(self) -> int: ...
    def reveal(self, index: int, animate: bool = True) -> None: ...
    def set_view(self, columns: int, scroll_rows: float) -> None: ...
    def view(self) -> tuple[int, float]: ...
    def set_columns(self, columns: int) -> None: ...
    def set_tile(self, px: float) -> None: ...
    def tile(self) -> float: ...
    def set_scroll_rows(self, rows: float) -> None: ...
    def set_captions(self, keys: list[str], names: list[str]) -> None: ...
    def set_caption_mode(self, mode: int) -> None: ...
    def cell_aspect(self) -> float: ...
    def stats(self) -> dict[str, Any]: ...
    def set_caption(self, text: str) -> None: ...
    def after_frame(self, fn: Callable[[], None]) -> None: ...
    def set_new(self, new: NewPill) -> None: ...

    # callbacks the host installs; the adapter wires them to the item's signals
    on_selected: Callable[[int, int], None] | None
    on_activated: Callable[[int], None] | None
    on_quick_look: Callable[[int], None] | None
    on_view_changed: Callable[[], None] | None
    on_gesture_started: Callable[[], None] | None
    on_gesture_ended: Callable[[], None] | None
    on_wanted_changed: Callable[[], None] | None
    on_drag: Callable[[int, int], None] | None


class GalleryBridge(QObject):
    """Handed to ``GalleryView.qml`` as ``bridge``: what the QML overlay (hover preview,
    quick look, context menu) needs from the host without knowing Python."""

    #: the context menu's Quick look: the QML overlay shows that tile
    quickLookRequested = Signal(int)

    def __init__(self, host: GalleryHost) -> None:
        super().__init__()
        self._host = host

    @Slot(int, result=str)
    def previewUrl(self, index: int) -> str:
        return self._host.preview_url(int(index))

    @Slot(int, result=str)
    def captionOf(self, index: int) -> str:
        return self._host.caption_of(int(index))

    @Slot(int, result=bool)
    def isImage(self, index: int) -> bool:
        return self._host.is_image(int(index))

    @Slot(int)
    def openPreview(self, index: int) -> None:
        self._host.open_preview(int(index))

    @Slot(int)
    def openImage(self, index: int) -> None:
        self._host.open_image(int(index))

    @Slot(int, float, float)
    def contextMenu(self, index: int, x: float, y: float) -> None:
        """Right-click on a tile at a window point: its context menu (#154)."""
        self._host.open_context_menu({"kind": "items", "index": int(index)}, float(x), float(y))

    @Slot(str)
    def setSort(self, by: str) -> None:
        self._host.set_sort(str(by))

    @Slot()
    def ackArrivals(self) -> None:
        """The "+N new" pill retired (its items in view, the burst quiet): clear it."""
        self._host.ack_arrivals()


class NativeGallery:
    """Adapter over the ``GalleryItem`` QQuickItem inside ``GalleryView.qml``.

    PySide6 wraps the C++ item as ``QQuickItem`` and exposes its invokables and signals
    through the meta-object (``item.missing(...)``, ``item.userSelected.connect``)."""

    def __init__(self, root: QQuickItem) -> None:
        self.root = root
        g = root.findChild(QQuickItem, "gallery")
        if g is None:
            raise SpectoError("GalleryView.qml has no child named 'gallery'")
        self.item: QQuickItem = g
        self.on_selected: Callable[[int, int], None] | None = None
        self.on_activated: Callable[[int], None] | None = None
        self.on_quick_look: Callable[[int], None] | None = None
        self.on_view_changed: Callable[[], None] | None = None
        self.on_gesture_started: Callable[[], None] | None = None
        self.on_gesture_ended: Callable[[], None] | None = None
        self.on_wanted_changed: Callable[[], None] | None = None
        self.on_drag: Callable[[int, int], None] | None = None
        self._after_frame: Callable[[], None] | None = None  # the pending after_frame
        g.userSelected.connect(self._selected)
        g.activated.connect(self._activated)
        g.quickLook.connect(self._quick_look)
        g.userViewChanged.connect(self._view_changed)
        g.gestureStarted.connect(self._gesture_started)
        g.gestureEnded.connect(self._gesture_ended)
        g.wantedChanged.connect(self._wanted_changed)
        g.dragRequested.connect(self._drag)

    # -- signal relays ----------------------------------------------------------------
    def _selected(self, index: int, modifiers: int) -> None:
        if self.on_selected:
            self.on_selected(int(index), int(modifiers))

    def _activated(self, index: int) -> None:
        if self.on_activated:
            self.on_activated(int(index))

    def _quick_look(self, index: int) -> None:
        if self.on_quick_look:
            self.on_quick_look(int(index))

    def _view_changed(self) -> None:
        if self.on_view_changed:
            self.on_view_changed()

    def _gesture_started(self) -> None:
        if self.on_gesture_started:
            self.on_gesture_started()

    def _gesture_ended(self) -> None:
        if self.on_gesture_ended:
            self.on_gesture_ended()

    def _wanted_changed(self) -> None:
        if self.on_wanted_changed:
            self.on_wanted_changed()

    def _drag(self, index: int, modifiers: int) -> None:
        if self.on_drag:
            self.on_drag(int(index), int(modifiers))

    # -- item API ----------------------------------------------------------------------
    def set_count(self, n: int) -> None:
        self.item.setProperty("count", int(n))

    def count(self) -> int:
        return int(self.item.property("count"))

    def wanted(self) -> tuple[int, int, int]:
        p = self.item.property
        return int(p("firstWanted")), int(p("lastWanted")), int(p("wantTier"))

    def visible(self) -> tuple[int, int]:
        p = self.item.property
        return int(p("firstVisible")), int(p("lastVisible"))

    def missing(self, first: int, last: int, tier: int) -> list[int]:
        return [int(i) for i in self.item.missing(int(first), int(last), int(tier))]

    def push_shm(self, index: int, shm: str, offset: int, w: int, h: int) -> bool:
        return bool(self.item.pushThumbShm(int(index), str(shm), int(offset), int(w), int(h)))

    def push_bytes(self, index: int, rgba: bytes, w: int, h: int) -> bool:
        return bool(self.item.pushThumb(int(index), QByteArray(rgba), int(w), int(h)))

    def set_failed(self, index: int, failed: bool = True) -> None:
        self.item.setFailed(int(index), bool(failed))

    def clear(self) -> None:
        self.item.clearThumbs()

    def select(self, index: int) -> None:
        self.item.setProperty("selectedIndex", int(index))

    def selected(self) -> int:
        return int(self.item.property("selectedIndex"))

    def set_current(self, index: int) -> None:
        self.item.setProperty("currentIndex", int(index))

    def current(self) -> int:
        return int(self.item.property("currentIndex"))

    def reveal(self, index: int, animate: bool = True) -> None:
        self.item.revealIndex(int(index), bool(animate))

    def set_view(self, columns: int, scroll_rows: float) -> None:
        self.item.setView(int(columns), float(scroll_rows), False)

    def view(self) -> tuple[int, float]:
        p = self.item.property
        return int(p("effectiveColumns")), float(p("scrollRows"))

    def set_columns(self, columns: int) -> None:
        self.item.setProperty("columns", int(columns))

    def set_tile(self, px: float) -> None:
        self.item.setProperty("tileWidth", float(px))

    def tile(self) -> float:
        return float(self.item.property("tileWidth"))

    def set_scroll_rows(self, rows: float) -> None:
        self.item.setProperty("scrollRows", float(rows))

    def set_captions(self, keys: list[str], names: list[str]) -> None:
        self.item.setCaptions(list(keys), list(names))

    def set_caption_mode(self, mode: int) -> None:
        self.item.setProperty("captionMode", int(mode))

    def cell_aspect(self) -> float:
        return float(self.item.property("cellAspect"))

    def stats(self) -> dict[str, Any]:
        p = self.item.property
        return {
            k: p(k)
            for k in (
                "residentFull",
                "residentMini",
                "residentLarge",
                "staged",
                "uploads",
                "evictions",
                "frames",
                "syncMs",
                "drawCalls",
                "quads",
                "captions",
                "effectiveColumns",
                "tileWidth",
                "rows",
                "cellWidth",
                "cellHeight",
            )
        }

    def set_caption(self, text: str) -> None:
        self.root.setProperty("caption", text)

    def after_frame(self, fn: Callable[[], None]) -> None:
        """Call ``fn`` once, after the item's next frame (its stats land on the GUI thread
        after each one); a report from a frame drawn before this call does not count."""
        item = self.item
        before = int(item.property("frames"))
        self._drop_after_frame()  # a call not yet run gives way to this one

        def once() -> None:
            if int(item.property("frames")) <= before:
                return
            self._drop_after_frame()
            fn()

        self._after_frame = once
        item.statsChanged.connect(once)

    def _drop_after_frame(self) -> None:
        once, self._after_frame = self._after_frame, None
        if once is not None:
            with contextlib.suppress(RuntimeError, TypeError):
                self.item.statsChanged.disconnect(once)

    def set_new(self, new: NewPill) -> None:
        self.root.setProperty("newCount", int(new.count))
        self.root.setProperty("newFirst", int(new.first))

    def cell_rect(self, index: int) -> Any:
        """The tile's cell in scene coordinates (``QRectF``), empty when scrolled out."""
        r = self.item.cellRectOf(int(index))
        return self.item.mapRectToScene(r) if not r.isEmpty() else r


class GalleryHost(QuickViewHost):
    """One live gallery view; see the module docstring."""

    #: paused, the item leaves the scene and its atlas goes with its node (SPEC-3)
    release_when_paused = True

    def __init__(
        self,
        ctx: HostContext,
        view_id: str,
        manifest: ViewManifest,
        *,
        gallery: GalleryItemLike | None = None,
        jobs: JobManager | None = None,
        batch: int = DEFAULT_BATCH,
        max_inflight: int | None = None,
    ) -> None:
        super().__init__(ctx, view_id, manifest)
        self.jobs: JobManager = jobs or ctx.engine.jobs
        self.batch = max(1, batch)
        self.max_inflight = max_inflight or _default_inflight(self.jobs)
        self.collection: Collection | None = None
        self.items: list[Item] = []
        self.keys: list[str] = []
        self.sort = ""  # params.sort: "", key, name, time, with "-" for descending
        self.names: list[str] = []
        self._index_of: dict[str, int] = {}
        # the current item's key (the frame at the gallery's time, SPEC-6): kept by key so
        # a re-sort or a reload finds it again
        self._current_key: str | None = None
        self._refs: dict[int, DataRef | None] = {}
        self._inflight: dict[str, list[int]] = {}  # job id -> indices
        self._inflight_idx: set[int] = set()
        self._sigs: list[str] = []  # each item's stat as the collection gave it
        self.decoded = 0
        self.failed = 0
        self.pushed_bytes = 0
        # where the thumbnails came from: rendered by a job, the disk cache (big images
        # too, SPEC-17), memory; an image decoded afresh is not counted
        self.thumbs = {"rendered": 0, "disk": 0, "memory": 0}
        self._memory = thumbs.memory_for(ctx.engine)
        self._cache_dir = thumbs.cache_dir_for(ctx.project_root)
        # thumbnails of files gone or changed leave the disk cache, once a day (SPEC-17)
        thumbs.maybe_prune(self.jobs, self._cache_dir, ctx.project_root)
        self._palette: dict[str, str] | None = None
        self._gesture = False
        self._pump_timer: QTimer | None = None
        self._pump_pending = False
        self._in_pump = False
        # the "+N new" pill (SPEC-59): the keys of the items that arrived and are not
        # seen yet, and the arrival entries ("<source>:<rel>") no delivered collection
        # has held yet, each with the deliveries it has waited through
        self._new_keys: set[str] = set()
        self._pending: dict[str, int] = {}
        self.bridge = GalleryBridge(self)

        if gallery is None:
            gallery = self._create_native()
        self.gallery: GalleryItemLike = gallery
        gallery.on_selected = self._on_user_selected
        gallery.on_activated = self._on_activated
        gallery.on_quick_look = self._on_quick_look
        gallery.on_view_changed = self._on_user_view_changed
        gallery.on_gesture_started = self._on_gesture_started
        gallery.on_gesture_ended = self._on_gesture_ended
        gallery.on_wanted_changed = self.schedule_pump
        gallery.on_drag = self._on_drag
        self._apply_params()
        self.reload()

    # -- construction ------------------------------------------------------------------
    def _create_native(self) -> NativeGallery:
        if self.ctx.qml_engine is None:
            raise SpectoError("gallery host needs a QML engine (pass gallery= when headless)")
        if not gallery_native.is_built():
            raise SpectoError(
                "native plugin Specto.Native is not built", fix=gallery_native.BUILD_HINT
            )
        gallery_native.ensure_import_path(self.ctx.qml_engine)
        item = self.ctx.create_item(
            gallery_native.GALLERY_QML,
            properties={"viewId": self.view_id, "bridge": self.bridge},
        )
        self.item = item
        return NativeGallery(item)

    # -- ports -------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        if port == DATA:
            self.collection = value if isinstance(value, Collection) else None
            self.reload()
        elif port == SELECTION:
            self._show_selection(value)
        elif port == TIME and isinstance(value, TimeValue):
            self._follow_key(self.ctx.links.global_clock.nearest(self.view_id, value))

    def _show_selection(self, value: Any) -> None:
        key = None
        if isinstance(value, Selection):
            key = value.picked
        elif isinstance(value, dict):
            key = value.get("last") or (value.get("keys") or [None])[-1]
        self._show_key(key)

    def _show_key(self, key: str | None) -> None:
        i = self.index_of(str(key)) if key is not None else -1
        if i >= 0 and i != self.gallery.selected():
            self.gallery.select(i)
            self.gallery.reveal(i, True)
            self._caption()

    def _follow_key(self, key: str | None) -> None:
        """A time arriving: the frame nearest to it becomes the current item, marked and
        revealed; the selection stays the user's pick (following is not selecting)."""
        i = self.index_of(str(key)) if key is not None else -1
        if i < 0:
            return
        self._current_key = self.keys[i]
        if i != self.gallery.current():
            self.gallery.set_current(i)
            self.gallery.reveal(i, True)

    # -- items -------------------------------------------------------------------------
    def reload(self) -> None:
        """(Re)read the collection, reset the item and start decoding. A collection
        that merely grew keeps the thumbnails, scroll and selection it already has."""
        items = self._ordered(list(self.collection.items) if self.collection is not None else [])
        new_keys = [it.key for it in items]
        new_sigs = [_stat_sig(it) for it in items]
        old_n = len(self.keys)
        selected_key = (
            self.keys[self.gallery.selected()] if 0 <= self.gallery.selected() < old_n else None
        )
        # a changed file under a kept key needs its thumbnail again: not a mere growth
        grew = (
            0 < old_n <= len(new_keys)
            and new_keys[:old_n] == self.keys
            and new_sigs[:old_n] == self._sigs
        )
        if not grew:
            self._cancel_inflight()
            self.gallery.clear()
            self._refs = {}
            self.decoded = self.failed = 0
            self.thumbs = dict.fromkeys(self.thumbs, 0)
        self.items = items
        self.keys = new_keys
        self._sigs = new_sigs
        self.names = [_image_name(it) for it in items]
        self._index_of = {k: i for i, k in enumerate(self.keys)}
        self._take_arrivals()
        self.gallery.set_count(len(self.items))
        self.gallery.set_captions(self.keys, self.names)
        current = self._index_of.get(self._current_key, -1) if self._current_key else -1
        if current != self.gallery.current():
            self.gallery.set_current(current)
        restored = False
        if not grew:
            i = self._index_of.get(selected_key, -1) if selected_key else -1
            if i < 0 and self.items:
                # a reopened project: the frame remembered in params is picked again
                try:
                    stored = self.params.get("selected")
                except SpectoError:  # a host with no document entry (tests)
                    stored = None
                i = self._index_of.get(str(stored), -1) if stored else -1
                restored = i >= 0
            if i != self.gallery.selected():
                self.gallery.select(i)
            if restored:
                self.gallery.reveal(i, True)
                self.emit(SELECTION, Selection(keys=[self.keys[i]], last=self.keys[i]))
        if not self.items:
            self.set_health("paused", "no items" if self.collection is not None else "no data")
        else:
            self.set_health("loading", f"{len(self.items)} items, decoding")
        self._caption()
        self.schedule_pump()

    # -- arrivals: the "+N new" pill (SPEC-59) ------------------------------------------
    def on_arrivals(self, payload: dict[str, Any]) -> None:
        """Files arrived in a folder an intaker of this view watches (``link.arrivals``):
        its ``arrived`` entries wait for the collection that holds them. The oldest
        go once more than ``PENDING_CAP`` wait."""
        for entry in payload.get("arrived") or []:
            self._pending.setdefault(str(entry), 0)
        while len(self._pending) > PENDING_CAP:
            del self._pending[next(iter(self._pending))]

    def _take_arrivals(self) -> None:
        """After a reload (a delivery, whatever resolved it: the arrival's own
        collection, a sort or filter change, a rescan): an item holding a pending entry
        is new, every member of an arriving archive among them (``arrival_entry``
        names a member by its archive); an entry no item holds waits one more
        delivery, and after ``PENDING_DELIVERIES`` of them it is forgotten (a file the
        screener drops, a resolve that failed). Keys gone since drop out. Shows the
        count (at most the ``PENDING_CAP`` entries pending) and the first."""
        if self._pending:
            held: dict[str, set[str]] = {}
            for it in self.items:
                for rel in (it.meta.get("rel") or {}).values():
                    entry = arrival_entry(int(it.meta.get("source", 0)), str(rel))
                    held.setdefault(entry, set()).add(it.key)
            for entry in list(self._pending):
                keys = held.get(entry)
                if keys:
                    self._new_keys |= keys
                    del self._pending[entry]
                elif self._pending[entry] + 1 >= PENDING_DELIVERIES:
                    del self._pending[entry]
                else:
                    self._pending[entry] += 1
        self._new_keys &= self._index_of.keys()
        self._show_new()

    def pending_arrivals(self) -> int:
        """How many arrival entries wait for a collection that holds them."""
        return len(self._pending)

    def ack_arrivals(self) -> None:
        """The pill retired: nothing is new any more."""
        self._new_keys.clear()
        self._show_new()

    def _show_new(self) -> None:
        self.gallery.set_new(self._new_state())

    def _new_state(self) -> NewPill:
        if not self._new_keys:
            return NO_NEW
        return NewPill(len(self._new_keys), min(self._index_of[k] for k in self._new_keys))

    def _ordered(self, items: list[Item]) -> list[Item]:
        by, desc = self.sort.lstrip("-"), self.sort.startswith("-")
        if by == "key":
            items = sorted(items, key=lambda it: natural_key(it.key))
        elif by == "name":
            items = sorted(items, key=lambda it: natural_key(_image_name(it).lower()))
        elif by == "time":
            items = sorted(items, key=lambda it: int(it.meta.get("mtime_ns") or 0))
        else:
            return items
        return items[::-1] if desc else items

    def ref_of(self, index: int) -> DataRef | None:
        """The ``DataRef`` a tile shows: the item's first part in ``thumbs.KIND_ORDER``
        that the gallery accepts (cached; None when no part is one)."""
        if index in self._refs:
            return self._refs[index]
        from specto_readers import default_readers

        readers, item = default_readers(), self.items[index]
        accepts = set(self.manifest.accepts or ())
        ref = None
        for group in thumbs.KIND_ORDER:
            kinds = [k for k in group if not accepts or k in accepts]
            if kinds and (ref := readers.locate(item, accepts=kinds)) is not None:
                break
        self._refs[index] = ref
        return ref

    def index_of(self, key: str) -> int:
        return self._index_of.get(key, -1)

    def preview_url(self, index: int) -> str:
        """``file:`` URL of the image behind ``index`` (hover preview, quick look)."""
        if index < 0 or index >= len(self.items):
            return ""
        ref = self.ref_of(index)
        if ref is None:
            return ""
        if not thumbs.is_image(ref.kind):
            return self._cached_thumb_url(ref)
        from specto_app.members import ensure_provider, image_url_for

        ensure_provider(self.ctx.qml_engine)
        return image_url_for(ref.uri)

    def _cached_thumb_url(self, ref: DataRef) -> str:
        """A non-image tile previews as its largest thumbnail on disk ("" when none)."""
        if self._cache_dir is None:
            return ""
        for px in (LARGE_PX, THUMB_PX):
            path = thumbs.cache_path(self._cache_dir, ref.uri, px, self.palette)
            if path.is_file():
                return QUrl.fromLocalFile(str(path)).toString()
        return ""

    @property
    def palette(self) -> dict[str, str]:
        """The theme colours and fonts thumbnail cards are drawn with."""
        if self._palette is None:
            self._palette = thumbs.palette()
        return self._palette

    def caption_of(self, index: int) -> str:
        if index < 0 or index >= len(self.keys):
            return ""
        name = self.names[index]
        return f"{self.keys[index]}  {name}" if name else self.keys[index]

    def tile_rect(self, key: str) -> Any:
        """``shell.locate tile:<view>:<key>``: the tile's cell in scene coordinates, or
        None when the key is not in the collection or the tile is scrolled out."""
        i = self.index_of(key)
        cell = getattr(self.gallery, "cell_rect", None)
        if i < 0 or cell is None:
            return None
        r = cell(i)
        return None if r.isEmpty() else r

    # -- decode pump -------------------------------------------------------------------
    def schedule_pump(self) -> None:
        """Coalesce pump requests into one call on the next event-loop turn (or, without
        an application object, run them now, re-entrantly safe)."""
        if self.disposed:
            return
        if QCoreApplication.instance() is None:
            self._pump_pending = True
            if not self._in_pump:
                self.pump()
            return
        if self._pump_pending:
            return
        self._pump_pending = True
        if self._pump_timer is None:
            self._pump_timer = QTimer(self)
            self._pump_timer.setSingleShot(True)
            self._pump_timer.timeout.connect(self.pump)
        self._pump_timer.start(0)

    @Slot(result=int)
    def pump(self) -> int:
        """Ask the item what it lacks and queue decodes for it. Returns jobs submitted."""
        if self._in_pump:
            self._pump_pending = True
            return 0
        self._in_pump = True
        total = 0
        try:
            while True:
                self._pump_pending = False
                total += self._pump_once()
                if not self._pump_pending:
                    break
        finally:
            self._in_pump = False
        return total

    def _pump_once(self) -> int:
        if self.disposed or not self.items:
            return 0
        first, last, tier = self.gallery.wanted()
        if first < 0 or last < first:
            return 0
        budget = (self.max_inflight - len(self._inflight)) * self.batch
        if budget <= 0:
            return 0
        wanted = [i for i in self.gallery.missing(first, last, tier) if i not in self._inflight_idx]
        if not wanted:
            self._maybe_ready()
            return 0
        submitted = 0
        size = LARGE_PX if tier >= TIER_LARGE else THUMB_PX
        request = DecodeRequest(purpose="thumbnail", max_size=size)
        # images decode in batches of their own, every other kind renders in others
        chunks: dict[bool, tuple[list[int], list[DataRef]]] = {True: ([], []), False: ([], [])}
        for i in wanted[:budget]:
            ref = self.ref_of(i)
            if ref is None:
                self.gallery.set_failed(i)
                self.failed += 1
                continue
            image = thumbs.is_image(ref.kind)
            if not image and self._push_remembered(i, ref, size):
                continue
            if ref.kind == "video_ref" and is_virtual(ref.uri):
                # copied out of its archive first: a job of its own, so the batch never
                # waits on it and cancelling it cancels just that copy (SPEC-17)
                self._submit([i], [ref], request, False)
                submitted += 1
                continue
            chunk, refs = chunks[image]
            chunk.append(i)
            refs.append(ref)
            if len(chunk) == self.batch:
                self._submit(chunk, refs, request, image)
                submitted += 1
                chunks[image] = ([], [])
        for image, (chunk, refs) in chunks.items():
            if chunk:
                self._submit(chunk, refs, request, image)
                submitted += 1
        if not submitted:
            self._maybe_ready()
        return submitted

    def _push_remembered(self, index: int, ref: DataRef, px: int) -> bool:
        """A thumbnail this engine already made of this file and stat goes straight in."""
        hit = self._memory.get(ref.uri, px, thumbs.ref_stat(ref))
        if hit is None:
            return False
        w, h, rgba = hit
        if not self.gallery.push_bytes(index, rgba, w, h):
            return False
        self.decoded += 1
        self.thumbs["memory"] += 1
        return True

    def _submit(
        self, indices: list[int], refs: list[DataRef], request: DecodeRequest, image: bool
    ) -> None:
        idx = list(indices)
        self._inflight_idx.update(idx)
        px = int(request.max_size or THUMB_PX)

        def on_done(job: JobInfo) -> None:
            self._on_batch(idx, job, None if image else (refs, px))

        if image and self._cache_dir is not None:
            # big images keep their thumbnail on disk (SPEC-17)
            job = thumbs.submit_images(
                self.jobs, refs, request, self._cache_dir, canvas=self.view_id, on_done=on_done
            )
        elif image:
            from specto_readers import submit_decode_batch

            job = submit_decode_batch(
                self.jobs, refs, request, canvas=self.view_id, on_done=on_done
            )
        else:
            job = thumbs.submit_thumbs(
                self.jobs,
                refs,
                px,
                self._cache_dir,
                self.palette,
                canvas=self.view_id,
                on_done=on_done,
            )
        # a synchronous JobManager (InlineJobManager) has already run on_done here
        if not job.done:
            self._inflight[job.id] = idx

    def _on_batch(
        self,
        indices: list[int],
        job: JobInfo,
        rendered: tuple[list[DataRef], int] | None = None,
    ) -> None:
        """Push a batch's results; ``rendered`` (the refs and size of a thumbnail batch)
        also counts where each came from and keeps it in the engine's memory."""
        self._inflight.pop(job.id, None)
        self._inflight_idx.difference_update(indices)
        if self.disposed:
            _release(job.result)
            return
        if job.state != "done":
            for i in indices:
                self.gallery.set_failed(i)
            self.failed += len(indices)
            if job.state == "failed":
                self.set_health("error", f"decode failed: {(job.error or '').splitlines()[0]}")
            self.schedule_pump()
            return
        results = job.result if isinstance(job.result, list) else [job.result]
        for n, (i, res) in enumerate(zip(indices, results, strict=False)):
            if i >= len(self.items):
                continue
            if isinstance(res, dict) or res is None:
                self.gallery.set_failed(i)
                self.failed += 1
                continue
            spec = res.spec
            if len(spec.shape) != 3 or spec.shape[2] != 4:
                self.gallery.set_failed(i)
                self.failed += 1
                continue
            h, w = int(spec.shape[0]), int(spec.shape[1])
            ok = False
            if spec.shm:
                ok = self.gallery.push_shm(i, spec.shm, int(spec.offset), w, h)
            else:
                arr = getattr(res, "array", None)
                if arr is not None:
                    ok = self.gallery.push_bytes(i, arr.tobytes(), w, h)
            if ok:
                self.decoded += 1
                self.pushed_bytes += spec.nbytes
                if rendered is not None:
                    self._remember(rendered[0][n], rendered[1], res)
                elif res.spec.meta.get("cache") == "disk":
                    self.thumbs["disk"] += 1  # a big image's thumbnail read back
            else:
                self.gallery.set_failed(i)
                self.failed += 1
        _release(job.result)
        self._maybe_ready()
        self.schedule_pump()

    def _remember(self, ref: DataRef, px: int, res: Any) -> None:
        """Count where a pushed thumbnail came from and keep its pixels in memory."""
        meta = res.spec.meta
        how = meta.get("cache")
        if how in self.thumbs:
            self.thumbs[how] += 1
        arr = getattr(res, "array", None)
        stat = meta.get("stat")
        if arr is not None and stat is not None:
            h, w = int(res.spec.shape[0]), int(res.spec.shape[1])
            self._memory.put(ref.uri, px, tuple(stat), w, h, arr.tobytes())

    def _maybe_ready(self) -> None:
        loading = self.ctx.health_of(self.view_id).get("status") == "loading"
        if self.items and not self._inflight and loading:
            msg = f"{self.decoded} thumbnails" + (f", {self.failed} failed" if self.failed else "")
            self.set_health("ok", msg)
            self._caption()

    def _cancel_inflight(self) -> None:
        for jid in list(self._inflight):
            with contextlib.suppress(Exception):
                self.jobs.cancel(jid)
        self._inflight.clear()
        self._inflight_idx.clear()

    @property
    def inflight(self) -> int:
        return len(self._inflight)

    # -- user intent -> the selection port -------------------------------------------------
    def _on_user_selected(self, index: int, modifiers: int) -> None:
        if index < 0 or index >= len(self.keys):
            return
        key = self.keys[index]
        self._caption()
        self.emit(SELECTION, Selection(keys=[key], last=key))
        self.ctx.live.remember(self.view_id, {"selected": key})  # navigation: no undo step
        time = self.ctx.links.global_clock.time_of(self.view_id, key)
        if time is not None:
            # the picked frame's time is the clock's now: it is the current item here too
            self._current_key = key
            self.gallery.set_current(index)
            self.emit(TIME, time)

    def is_image(self, index: int) -> bool:
        """The tile shows an image (an image view shows it), not a thumbnail of another
        kind (#105)."""
        if index < 0 or index >= len(self.items):
            return False
        ref = self.ref_of(index)
        return ref is not None and thumbs.is_image(ref.kind)

    def _on_activated(self, index: int) -> None:
        """Double-click or Enter: select the tile and open the gallery's preview, which
        shows every kind, images too (#106, #115); one per gallery, a second activation
        only moves the pick."""
        self.open_preview(index)

    def open_image(self, index: int) -> str | None:
        """The context menu's "Open image": select the tile and open the gallery's image
        view beside it (one per gallery, like the preview)."""
        if index < 0 or index >= len(self.keys):
            return None
        self._pick(index)
        return self.open_image_view()

    def _pick(self, index: int) -> None:
        if self.gallery.selected() != index:
            self.gallery.select(index)
        self._on_user_selected(index, 0)

    def open_preview(self, index: int) -> str | None:
        """Pick the tile and show it in the gallery's preview (opened as a user submit
        when missing): a double-click or the quick look of a tile that is not an image."""
        if index < 0 or index >= len(self.keys):
            return None
        self._pick(index)
        try:
            vid, _res = open_gallery_preview(self.ctx.live, self.view_id)
        except SpectoError as e:
            self.ctx.notice(e, "open preview")
            return None
        return vid

    def open_image_view(self) -> str | None:
        """The image view beside this gallery (opened as a user submit when missing)."""
        try:
            vid, _res = open_image_view(self.ctx.live, self.view_id)
        except SpectoError as e:
            self.ctx.notice(e, "open image")
            return None
        return vid

    # -- sort (the footer pills) --------------------------------------------------------------
    def set_sort(self, by: str) -> str:
        """Sort by ``by`` (key, name, time); the active sort again flips the direction.
        Applied now and written to ``params.sort`` as a user submit."""
        if by not in SORTS:
            return self.sort
        new = f"-{by}" if self.sort == by else by
        self._set_sort(new)
        doc = self.ctx.live.document()
        view = doc["views"].get(self.view_id)
        if view is not None:
            view.setdefault("params", {})["sort"] = new
            desc = ", descending" if new.startswith("-") else ""
            self.ctx.submit(doc, f"sort {view.get('label') or self.view_id} by {by}{desc}")
        return new

    def _set_sort(self, sort: str) -> None:
        if sort == self.sort:
            return
        self.sort = sort
        if self.item is not None:
            self.item.setProperty("sortBy", sort.lstrip("-"))
            self.item.setProperty("sortDesc", sort.startswith("-"))
        self.reload()

    # -- context menu (#154) -------------------------------------------------------------------
    def context_target(self, hit: dict[str, Any] | None) -> MenuTarget | None:
        """The right-clicked tile (no pick: a right-click changes nothing), or the picked
        one for the keyboard; ``data.index`` is its index, ``data.image`` whether it shows
        an image."""
        if hit is None:
            index = self.gallery.selected()
        elif hit.get("kind") == "items":
            index = int(hit.get("index", -1))
        else:
            return super().context_target(hit)
        if not 0 <= index < len(self.items):
            return self.menu_target("view") if hit is None else None
        ref = self.ref_of(index)
        path = ref.uri if ref is not None else self.items[index].primary
        file, sep, inner = path.partition("#")  # a container entry keeps its address
        item = MenuItemRef(
            key=self.keys[index],
            path=os.path.normpath(file) + sep + inner,
            kind=ref.kind if ref is not None else "",
        )
        return self.menu_target("items", [item], {"index": index, "image": self.is_image(index)})

    # -- drag out (#185) ------------------------------------------------------------------------
    def drag_payload(self, hit: dict[str, Any] | None) -> DragPayload | None:
        """The dragged tile, as its menu's target: an ``image`` when it shows an image,
        else a ``file``."""
        target = self.context_target(hit)
        if target is None or not target.items:
            return None
        return payload_for(target, kind="image" if target.data.get("image") else "file")

    def _on_drag(self, index: int, modifiers: int) -> None:
        """A left mouse drag left tile ``index`` (no pick: the selection stays)."""
        self.start_drag({"kind": "items", "index": index})

    def context_anchor(self, target: MenuTarget) -> tuple[float, float] | None:
        """Inside the tile's cell when it shows, else the default."""
        index = int(target.data.get("index", -1))
        cell = getattr(self.gallery, "cell_rect", None)
        r = cell(index) if cell is not None and index >= 0 else None
        if r is None or r.isEmpty():
            return super().context_anchor(target)
        return r.x() + r.width() / 2, r.y() + r.height() / 2

    def quick_look(self, index: int) -> None:
        """The context menu's Quick look: what Space does on the picked tile."""
        if 0 <= index < len(self.items):
            self.bridge.quickLookRequested.emit(index)

    def _on_quick_look(self, index: int) -> None:
        """Space: the QML overlay toggles its quick look; nothing to journal."""

    def _on_user_view_changed(self) -> None:
        """Scroll is the view's own business (no pan/zoom port); a zoom outside a gesture
        is remembered now, one inside a gesture when it ends."""
        if not self._gesture:
            self._remember_tile()

    def _on_gesture_started(self) -> None:
        self._gesture = True

    def _on_gesture_ended(self) -> None:
        self._gesture = False
        self._remember_tile()

    def _remember_tile(self) -> None:
        """The user's thumbnail size into ``params.tile`` (#173): navigation, no undo step."""
        tile = round(self.gallery.tile(), 1)
        if tile <= 0:
            return
        try:
            stored = self.params.get("tile")
        except SpectoError:  # a host with no document entry (tests)
            return
        if not (_positive(stored) and abs(float(stored) - tile) < TILE_EPS):
            self.ctx.live.remember(self.view_id, {"tile": tile})

    def select_key(self, key: str, *, emit: bool = True) -> int:
        """Select and reveal ``key`` as if the user clicked it (tools, tests). Returns the
        index, -1 when the key is not in the collection."""
        i = self.index_of(key)
        if i < 0:
            return -1
        self.gallery.select(i)
        self.gallery.reveal(i, True)
        if emit:
            self._on_user_selected(i, 0)
        else:
            self._caption()
        return i

    # -- contract ----------------------------------------------------------------------
    def on_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        """Re-apply the params a patch touches, only those: a pick's ``params.selected``
        write must not put back a stale size over the user's zoom (#173)."""
        prefix = f"/views/{self.view_id}"
        keys: set[str] = set()
        for op in patch:
            path = op.get("path", "")
            if path in (prefix, f"{prefix}/params"):
                self._apply_params()
                return
            if path.startswith(f"{prefix}/params/"):
                keys.add(path[len(prefix) + 8 :].split("/", 1)[0])
        if keys:
            self._apply_params(keys)

    def _apply_params(self, keys: set[str] | None = None) -> None:
        """``params.tile`` (thumbnail width in px, else ``params.columns``), ``captions``
        and ``sort`` from the document; ``keys`` limits it to the params that changed."""
        try:
            p = self.params
        except SpectoError:
            return

        def wanted(*names: str) -> bool:
            return keys is None or any(n in keys for n in names)

        if wanted("tile", "columns"):
            tile, cols = p.get("tile"), p.get("columns")
            if _positive(tile) and (keys is None or "tile" in keys or not _positive(cols)):
                if abs(self.gallery.tile() - float(tile)) >= TILE_EPS:
                    self.gallery.set_tile(float(tile))
            elif _positive(cols):
                self.gallery.set_columns(int(cols))
        mode = p.get("captions")
        if isinstance(mode, bool):
            mode = 1 if mode else 0
        if wanted("captions") and isinstance(mode, int | float):
            self.gallery.set_caption_mode(int(mode))
        sort = p.get("sort")
        if wanted("sort") and isinstance(sort, str) and sort.lstrip("-") in (*SORTS, ""):
            self._set_sort(sort)

    def snapshot(self) -> dict[str, Any]:
        first, last = self.gallery.visible()
        cols, rows = self.gallery.view()
        sel = self.gallery.selected()
        cur = self.gallery.current()
        wf, wl, tier = self.gallery.wanted()
        keys = self.keys[first : last + 1] if first >= 0 else []
        stats = self.gallery.stats()
        return {
            **super().snapshot(),
            "count": len(self.items),
            "sort": self.sort,
            # the "+N new" pill: arrivals not seen yet, the index of the first (SPEC-59)
            "new": self._new_state()._asdict(),
            "collection": {
                "link": self.collection.link if self.collection else None,
                "generation": self.collection.generation if self.collection else None,
            },
            "selected": {
                "index": sel,
                "key": self.keys[sel] if 0 <= sel < len(self.keys) else None,
            },
            # the frame at the gallery's time (a time link), marked apart from the selection
            "current": {
                "index": cur,
                "key": self.keys[cur] if 0 <= cur < len(self.keys) else None,
            },
            "view": {
                "columns": cols,
                "tile": round(self.gallery.tile(), 1),
                "scroll_rows": rows,
                "cell_aspect": self.gallery.cell_aspect(),
            },
            "visible": {
                "first": first,
                "last": last,
                "keys": keys[:SNAPSHOT_KEYS_CAP],
                "truncated": len(keys) > SNAPSHOT_KEYS_CAP,
            },
            "wanted": {"first": wf, "last": wl, "tier": tier},
            "decode": {
                "decoded": self.decoded,
                "failed": self.failed,
                "inflight_jobs": len(self._inflight),
                "inflight_items": len(self._inflight_idx),
                "bytes": self.pushed_bytes,
                "thumbs": dict(self.thumbs),
            },
            "resident": {
                "mini": stats.get("residentMini"),
                "full": stats.get("residentFull"),
                "large": stats.get("residentLarge"),
            },
            "stats": stats,
        }

    # -- pause / resume (contracts sections 11, 12) ----------------------------------------
    def on_restore(self) -> None:
        """Released, the item went back into the scene with an empty atlas: its first
        frame there finds nothing resident, so the pump decodes what the view shows again
        once that frame is drawn."""
        self.gallery.after_frame(self.schedule_pump)

    def dispose(self) -> None:
        if self.disposed:
            return
        self._cancel_inflight()
        if self._pump_timer is not None:
            self._pump_timer.stop()
        super().dispose()

    # -- helpers -----------------------------------------------------------------------
    def _caption(self) -> None:
        """The footer's picked item (#126): its key, nothing while none is picked (the
        footer's position says how many there are)."""
        sel = self.gallery.selected()
        self.gallery.set_caption(self.keys[sel] if 0 <= sel < len(self.keys) else "")


def _image_name(item: Item) -> str:
    """The file name of the item's image part (first image-like part, else any part)."""
    parts = item.parts or {}
    for role, path in parts.items():
        if role.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"):
            return Path(path).name
    for path in parts.values():
        return Path(path).name
    return ""


_NUM_RE = re.compile(r"(\d+)")


def natural_key(text: str) -> tuple[Any, ...]:
    """``"img10"`` after ``"img9"``: digits compare as numbers."""
    return tuple(int(t) if t.isdigit() else t for t in _NUM_RE.split(text))


def _stat_sig(item: Item) -> str:
    """What the collection says of an item's files (size and time per part)."""
    meta = item.meta or {}
    return repr((meta.get("stat"), meta.get("mtime_ns"), meta.get("size")))


def _positive(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and v > 0


def _default_inflight(jobs: JobManager) -> int:
    size = getattr(jobs, "size", None)
    return max(2, int(size) * 2) if isinstance(size, int) else 2


def _release(result: Any) -> None:
    try:
        from specto_readers.pool import _release_all

        _release_all(result)
    except Exception:
        pass


class GalleryFactory:
    """``ViewHostFactory`` for ``gallery``; the manifest is core's built-in one."""

    type = "gallery"
    plugin = GALLERY_PLUGIN

    def create(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> GalleryHost:
        return GalleryHost(ctx, view_id, manifest)


def factory() -> GalleryFactory:
    return GalleryFactory()


def _tile(call: MenuCall) -> int:
    return int(call.target.data.get("index", -1))


#: the gallery's own context-menu entries (#154); the browsers' Copy path entries follow
MENU_ENTRIES = (
    MenuEntry(
        id="gallery.open_preview",
        label="Open preview",
        shortcut="Enter",
        group="open",
        view_types=("gallery",),
        handler=lambda call: call.host.open_preview(_tile(call)),
    ),
    MenuEntry(
        id="gallery.open_image",
        label="Open image",
        icon="image",
        group="open",
        order=1,
        view_types=("gallery",),
        enabled=lambda t: t.data.get("image") is True,
        handler=lambda call: call.host.open_image(_tile(call)),
    ),
    MenuEntry(
        id="gallery.quick_look",
        label="Quick look",
        icon="eye",
        shortcut="Space",
        group="open",
        order=2,
        view_types=("gallery",),
        handler=lambda call: call.host.quick_look(_tile(call)),
    ),
)
for _entry in MENU_ENTRIES:
    default_registry().add(_entry, replace=True)


def register(registry: Any) -> None:
    """Entry point / ``--host-module`` hook: ``registry.add_factory(GalleryFactory())``."""
    registry.add_factory(factory())


__all__ = [
    "DATA",
    "GALLERY_PLUGIN",
    "LARGE_PX",
    "MENU_ENTRIES",
    "SELECTION",
    "SORTS",
    "THUMB_PX",
    "TIER_FULL",
    "TIER_LARGE",
    "TIER_MINI",
    "TIME",
    "GalleryBridge",
    "GalleryFactory",
    "GalleryHost",
    "GalleryItemLike",
    "NativeGallery",
    "factory",
    "natural_key",
    "register",
]
