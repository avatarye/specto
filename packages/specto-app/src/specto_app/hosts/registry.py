"""The view-host registry: view type -> factory, view id -> live host.

Other builders plug in one of three ways, all before the shell opens a canvas:

1. ``specto_app.hosts.register_factory(factory)`` from Python (tests, in-process).
2. An entry point in the ``specto.view_hosts`` group whose object is a factory
   (``type`` + ``create``) or a callable ``register(registry)``.
3. ``specto-app --host-module pkg.mod``: the module is imported and may do either.

The registry follows the canvas document (``engine.live``): a host is created on first
use for a view in the document, handed the ops under ``/views/<id>`` of every patch,
and disposed when the view leaves the document. Each host is attached to the link
runtime, which delivers its input ports (``receive``); a resolving link marks its target
view ``loading``, a failed one ``error``, a Python link awaiting the user's approval
``approval`` (#43; the view's Approve button calls ``CanvasController.approveView``). A
view whose type has no factory gets a ``MissingHost`` card with health ``error`` and a
fix-it message, never a crash.
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import inspect
from collections.abc import Callable, Iterable
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtQuick import QQuickItem

from specto_core import Engine
from specto_core.kinds import PluginManifest, ViewManifest
from specto_core.links import Collection, ContentValue

from .base import HostContext, QuickViewHost, ViewHostFactory

ENTRY_POINT_GROUP = "specto.view_hosts"
#: the empty-state line of a view that follows the file tree's picks (glossary: selection
#: intaker) while nothing is picked
PICK_HINT = "Pick files in the file tree"

_pending: list[ViewHostFactory | Callable[[HostRegistry], None]] = []


def register_factory(factory: ViewHostFactory | Callable[[HostRegistry], None]) -> None:
    """Queue a factory (or a ``register(registry)`` callable) for every registry created
    after this call, and add it to the live ones."""
    _pending.append(factory)
    for reg in list(HostRegistry._live):
        reg._adopt(factory)


def pending_factories() -> list[Any]:
    return list(_pending)


def clear_pending() -> None:
    _pending.clear()


class HostRegistry(QObject):
    """Owns the hosts of the one canvas. QML calls ``attach``/``detach`` from view slots."""

    hostCreated = Signal(str)
    hostDisposed = Signal(str)
    healthChanged = Signal(str)

    _live: list[HostRegistry] = []

    def __init__(self, ctx: HostContext, *, load_entry_points: bool = True) -> None:
        super().__init__()
        self.ctx = ctx
        self.engine: Engine = ctx.engine
        self.factories: dict[str, ViewHostFactory] = {}
        self._fallbacks: dict[str, list[ViewHostFactory]] = {}
        self.hosts: dict[str, Any] = {}  # QuickViewHost or any host with an ``item``
        self._slots: dict[str, QQuickItem] = {}
        self._parking: QQuickItem | None = None
        # view id -> health before the pause (status, message, hint); the registry owns
        # the pause bookkeeping
        self._paused: dict[str, dict[str, str]] = {}
        self._off_patch = ctx.live.on_patch(self._on_patch)
        self._off_events = ctx.live.events.subscribe(self._on_event)
        ctx.health_listeners.append(self.healthChanged.emit)
        HostRegistry._live.append(self)
        for f in _pending:
            self._adopt(f)
        if load_entry_points:
            self.load_entry_points()

    # -- factories --------------------------------------------------------------------
    def _adopt(self, obj: Any) -> None:
        if hasattr(obj, "type") and hasattr(obj, "create"):
            self.add_factory(obj)
        elif callable(obj):
            # ``register(registry)``, or a zero-argument function returning a factory
            if inspect.signature(obj).parameters:
                obj(self)
            else:
                self._adopt(obj())
        else:
            print(f"[WARN] host registration ignored: {obj!r} is neither a factory nor callable")

    def add_factory(self, factory: ViewHostFactory, *, override: bool = True) -> None:
        if factory.type in self.factories and not override:
            return
        prev = self.factories.get(factory.type)
        if prev is not None and prev is not factory:
            # the earlier factory stays as the fallback (plain-text diff behind Monaco)
            self._fallbacks.setdefault(factory.type, []).append(prev)
        self.factories[factory.type] = factory
        plugin: PluginManifest | None = getattr(factory, "plugin", None)
        if plugin is not None:
            self._register_plugin(plugin)

    def has_factory(self, view_type: str) -> bool:
        return view_type in self.factories

    def _register_plugin(self, plugin: PluginManifest) -> None:
        reg = self.engine.plugins
        if plugin.name in reg.plugins:
            return
        views = [v for v in plugin.views if reg.view(v.type) is None]
        if len(views) != len(plugin.views):
            plugin = plugin.model_copy(update={"views": views})
        try:
            reg.register(plugin)
        except ValueError as e:  # a second registry in the same process
            print(f"[WARN] plugin {plugin.name}: {e}")

    def load_entry_points(self) -> int:
        n = 0
        try:
            eps: Iterable[md.EntryPoint] = md.entry_points(group=ENTRY_POINT_GROUP)
        except Exception as e:  # pragma: no cover
            print(f"[WARN] entry points unavailable: {e}")
            return 0
        for ep in eps:
            try:
                self._adopt(ep.load())
                n += 1
            except Exception as e:
                print(f"[WARN] view host entry point {ep.name} failed: {type(e).__name__}: {e}")
        return n

    def load_module(self, name: str) -> None:
        """``--host-module``: import it; it registers itself (or exposes ``register``)."""
        mod = importlib.import_module(name)
        reg = getattr(mod, "register", None)
        if callable(reg):
            reg(self)

    # -- hosts ----------------------------------------------------------------------------
    def manifest_for(self, view_type: str) -> ViewManifest:
        return self.ctx.manifest_for(view_type)

    def host(self, view_id: str, *, create: bool = True) -> Any:
        h = self.hosts.get(view_id)
        if h is not None or not create:
            return h
        view = self.ctx.live.data["views"].get(view_id)
        if view is None:
            return None
        vtype = view["type"]
        manifest = self.manifest_for(vtype)
        factory = self.factories.get(vtype)
        try:
            if factory is None:
                h = MissingHost(
                    self.ctx, view_id, manifest, reason=f"no view host for type {vtype!r}"
                )
            else:
                h = self._create(factory, view_id, manifest)
        except Exception as e:  # a broken host must not take the shell down
            print(f"[ERROR] host for {view_id} ({vtype}) failed: {type(e).__name__}: {e}")
            h = MissingHost(self.ctx, view_id, manifest, reason=f"{type(e).__name__}: {e}")
        self.hosts[view_id] = h
        if isinstance(h, MissingHost):
            self.ctx.set_health(view_id, "error", h.reason)
        else:
            # the runtime may have asked before the host existed (a load, a submit)
            waiting = self.approval_pending(view_id)
            if waiting:
                lid, st = waiting[0]
                self.ctx.set_health(
                    view_id, "approval", f"{lid}: approve {st.get('path')} to let it run"
                )
        self.ctx.links.attach(
            view_id, lambda port, value, vid=view_id: self._deliver(vid, port, value)
        )
        # a host born while its input is still resolving says so (its own constructor
        # has just reported "no data")
        for lid, link in self.ctx.live.data["links"].items():
            if link["target"]["view"] == view_id and self.ctx.links.resolving(lid):
                self.ctx.set_health(view_id, "loading", f"resolving {_link_name(link)}")
        self.hostCreated.emit(view_id)
        return h

    def ensure_all(self) -> list[str]:
        """A host for every view in the document (the shell calls this after a load)."""
        return [vid for vid in list(self.ctx.live.data["views"]) if self.host(vid) is not None]

    def _create(self, factory: ViewHostFactory, view_id: str, manifest: ViewManifest) -> Any:
        """Create with ``factory``; when it fails, try the factories it replaced (newest
        first) before giving up, so a missing build (Monaco not vendored, native plugin
        not built) degrades to the simpler host of the same type."""
        try:
            return factory.create(self.ctx, view_id, manifest)
        except Exception as e:
            for prev in reversed(self._fallbacks.get(factory.type, [])):
                try:
                    h = prev.create(self.ctx, view_id, manifest)
                except Exception as e2:
                    print(f"[WARN] fallback host for {manifest.type} failed too: {e2}")
                    continue
                print(f"[WARN] host for {view_id} ({manifest.type}) fell back: {e}")
                return h
            raise

    def _deliver(self, view_id: str, port: str, value: Any) -> None:
        h = self.hosts.get(view_id)
        if h is None or getattr(h, "disposed", False) or isinstance(h, MissingHost):
            return  # a missing host keeps saying why it is missing
        try:
            h.receive(port, value)
        except Exception as e:
            self.ctx.set_health(view_id, "error", f"receive {port}: {type(e).__name__}: {e}")
            return
        if self._nothing_picked(value):
            picked = {"status": "paused", "message": "nothing picked", "hint": PICK_HINT}
            if view_id in self._paused:  # off screen: what ``resume`` brings back
                self._paused[view_id] = picked
            else:
                self.ctx.set_health(view_id, **picked)

    def _nothing_picked(self, value: Any) -> bool:
        """An empty value from a link off a file tree's ``selected_files`` (a selection
        intaker, a read_contents of the picks): the user has picked nothing yet, which
        the view says instead of staying blank."""
        if isinstance(value, Collection):
            empty = not value.items
        elif isinstance(value, ContentValue):
            empty = not value.entries
        else:
            return False
        if not empty:
            return False
        link = self.ctx.live.data["links"].get(value.link)
        if link is None or link["source"]["port"] != "selected_files":
            return False
        source = self.ctx.live.data["views"].get(link["source"]["view"])
        return source is not None and source["type"] == "file_tree"

    # -- the document and the links ----------------------------------------------------------
    def _on_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        if meta.get("kind") == "load":
            self.dispose_all(keep_registered=True)
            return
        views = self.ctx.live.data["views"]
        by_view: dict[str, list[dict[str, Any]]] = {}
        for op in patch:
            parts = op.get("path", "").split("/")
            if len(parts) >= 3 and parts[1] == "views":
                by_view.setdefault(parts[2], []).append(op)
        for vid, ops in by_view.items():
            if vid not in views:
                self.dispose(vid)
                continue
            h = self.hosts.get(vid)
            if h is None or getattr(h, "disposed", False):
                continue
            try:
                h.on_patch(ops, meta)
            except Exception as e:
                self.ctx.set_health(vid, "error", f"on_patch: {type(e).__name__}: {e}")

    def _on_event(self, ev: Any) -> None:
        if not ev.kind.startswith("link."):
            return
        target = ev.payload.get("target") or {}
        vid = target.get("view")
        if not vid or vid not in self.ctx.live.data["views"]:
            return
        if isinstance(self.hosts.get(vid), MissingHost):
            return  # it keeps saying why it is missing, whatever its links do
        link = self.ctx.live.data["links"].get(ev.payload.get("link"), {})
        what = _link_name(link)
        # a paused (off-screen) view keeps showing ``paused``: its link health goes to the
        # record ``resume`` restores, so a link that resolved meanwhile comes back ``ok``
        paused = vid in self._paused
        current = (self._paused[vid] if paused else self.ctx.health_of(vid))["status"]

        def set_health(status: str, message: str) -> None:
            if paused:
                self._paused[vid] = {"status": status, "message": message, "hint": ""}
            else:
                self.ctx.set_health(vid, status, message)

        if ev.kind == "link.arrivals":
            # watch mode: what the host shows stays until the new collection swaps in
            n = ev.payload.get("added", 0)
            gone = ev.payload.get("removed", 0)
            note = f"{n} arrived" if n else (f"{gone} removed" if gone else "files changed")
            set_health("loading", f"{note}, resolving {what}")
        elif ev.kind == "link.resolving":
            if current != "loading":
                set_health("loading", f"resolving {what}")
        elif ev.kind == "link.failed":
            set_health("error", f"{what}: {ev.payload.get('error', 'failed')}")
        elif ev.kind == "link.needs_approval":
            set_health("approval", f"{what}: approve {ev.payload.get('path')} to let it run")
        elif ev.kind == "link.resolved" and current == "loading":
            waiting = self.approval_pending(vid)
            if waiting:
                lid, st = waiting[0]
                set_health("approval", f"{lid}: approve {st.get('path')} to let it run")
            else:
                set_health("ok", "")

    def approval_pending(self, view_id: str) -> list[tuple[str, dict[str, Any]]]:
        """The view's input links whose Python file awaits approval, with their status."""
        out = []
        for lid, link in self.ctx.live.data["links"].items():
            if link["target"]["view"] != view_id:
                continue
            st = self.ctx.links.status(lid) or {}
            if st.get("state") == "needs_approval":
                out.append((lid, st))
        return out

    def route_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        """Hand each live host the ops under its own view (tests; the registry does this
        itself from the live canvas's patch stream)."""
        self._on_patch(patch, meta)

    def dispose(self, view_id: str) -> None:
        self._paused.pop(view_id, None)
        h = self.hosts.pop(view_id, None)
        self.ctx.links.detach(view_id)
        self._slots.pop(view_id, None)
        self.ctx.forget_health(view_id)
        if h is not None:
            try:
                dispose = getattr(h, "dispose", None)
                if callable(dispose):
                    dispose()
                elif getattr(h, "item", None) is not None:
                    h.item.setParentItem(None)
                    h.item.deleteLater()
            finally:
                self.hostDisposed.emit(view_id)

    def dispose_all(self, *, keep_registered: bool = False) -> None:
        for vid in list(self.hosts):
            self.dispose(vid)
        if not keep_registered:
            self._off_patch()
            self._off_events()
            if self.healthChanged.emit in self.ctx.health_listeners:
                self.ctx.health_listeners.remove(self.healthChanged.emit)
            if self in HostRegistry._live:
                HostRegistry._live.remove(self)

    def snapshot(self, view_id: str) -> dict[str, Any]:
        h = self.hosts.get(view_id)
        if h is None:
            return {}
        try:
            return h.snapshot()
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    # -- pause / resume (contracts section 16) ------------------------------------------------
    def pause(self, view_id: str, why: str = "off-screen") -> bool:
        """Tell a live host its view is off-screen (or collapsed, #114: ``why`` is the
        health message); health becomes ``paused`` (the previous health is kept for
        ``resume``). True when it was running."""
        h = self.hosts.get(view_id)
        if h is None or getattr(h, "disposed", False):
            return False
        if view_id in self._paused:
            if self.ctx.health_of(view_id).get("message") != why:
                self.ctx.set_health(view_id, "paused", why)  # off-screen, then collapsed
            return False
        self._paused[view_id] = {"status": "ok", "message": "", "hint": ""}
        self._paused[view_id].update(self.ctx.health_of(view_id))
        pause = getattr(h, "pause", None)
        try:
            if callable(pause):
                pause()
            self.ctx.set_health(view_id, "paused", "off-screen")
        except Exception as e:
            self.ctx.set_health(view_id, "error", f"pause: {type(e).__name__}: {e}")
        return True

    def resume(self, view_id: str) -> bool:
        h = self.hosts.get(view_id)
        before = self._paused.pop(view_id, None)
        if h is None or getattr(h, "disposed", False) or before is None:
            return False
        resume = getattr(h, "resume", None)
        try:
            if callable(resume):
                resume()
            # the health from before the pause, the empty-state hint with it (#91)
            self.ctx.set_health(view_id, before["status"], before["message"], before["hint"])
        except Exception as e:
            self.ctx.set_health(view_id, "error", f"resume: {type(e).__name__}: {e}")
        return True

    def is_paused(self, view_id: str) -> bool:
        return view_id in self._paused

    def snapshot_image(self, view_id: str) -> Any:
        """The host's own image, or None (the shell then crops a window grab)."""
        h = self.hosts.get(view_id)
        fn = getattr(h, "snapshot_image", None)
        if h is None or not callable(fn):
            return None
        try:
            return fn()
        except Exception as e:
            print(f"[WARN] snapshot_image {view_id}: {type(e).__name__}: {e}")
            return None

    # -- slots (called from QML) ----------------------------------------------------------
    def set_parking(self, item: QQuickItem | None) -> None:
        """A hidden item that keeps detached host items alive and off screen."""
        self._parking = item

    @Slot(str, QQuickItem, result=bool)
    def attach(self, view_id: str, slot: QQuickItem) -> bool:
        h = self.host(view_id)
        item = getattr(h, "item", None) if h is not None else None
        if item is None:
            return False
        self._slots[view_id] = slot
        item.setParentItem(slot)
        item.setX(0)
        item.setY(0)
        item.setWidth(slot.width())
        item.setHeight(slot.height())
        item.setVisible(True)

        def fit() -> None:
            if item.parentItem() is slot:
                item.setWidth(slot.width())
                item.setHeight(slot.height())

        slot.widthChanged.connect(fit)
        slot.heightChanged.connect(fit)
        return True

    @Slot(str, QQuickItem)
    def detach(self, view_id: str, slot: QQuickItem) -> None:
        # the QML scene can outlive this wrapper at shutdown (slots detach on destruction)
        h = (getattr(self, "hosts", None) or {}).get(view_id)
        item = getattr(h, "item", None) if h is not None else None
        if item is None:
            return
        if item.parentItem() is slot:
            item.setParentItem(self._parking)
            item.setVisible(False)
        if self._slots.get(view_id) is slot:
            self._slots.pop(view_id, None)

    @Slot(str, result=bool)
    def isAttached(self, view_id: str) -> bool:
        return view_id in self._slots

    @Slot(str, result="QVariantMap")
    def healthOf(self, view_id: str) -> dict[str, str]:
        return self.ctx.health_of(view_id)

    def slot_of(self, view_id: str) -> QQuickItem | None:
        return self._slots.get(view_id)


def _link_name(link: dict[str, Any]) -> str:
    return link.get("label") or link.get("kind") or "link"


class MissingHost(QuickViewHost):
    """Placeholder for a view type without a host: a card saying why, health ``error``."""

    def __init__(self, ctx: HostContext, view_id: str, manifest: ViewManifest, *, reason: str):
        super().__init__(ctx, view_id, manifest)
        self.reason = reason
        if ctx.qml_engine is not None:
            try:
                self.item = ctx.create_item(
                    "MissingView.qml",
                    properties={
                        "viewId": view_id,
                        "viewType": manifest.type,
                        "reason": reason,
                        "fix": "register a view host for this type (specto.view_hosts), "
                        "or close the view",
                    },
                )
            except Exception as e:  # pragma: no cover - only when the QML module is broken
                print(f"[ERROR] MissingView.qml: {e}")
                self.item = None

    def snapshot(self) -> dict[str, Any]:
        return {**super().snapshot(), "missing_host": self.reason}


__all__ = [
    "ENTRY_POINT_GROUP",
    "HostRegistry",
    "MissingHost",
    "clear_pending",
    "pending_factories",
    "register_factory",
]
