"""View hosts: the app side of contracts section 8, on ports (ADR 0014).

A **view host** is one live view instance: Python glue (no per-frame work) that owns one
``QQuickItem`` and implements ``receive(port, value)``, ``on_patch`` and ``snapshot``.
It emits values on the view's output ports (``emit``); the link runtime
(``specto_core.links``) resolves the links fed from them and delivers the results to
the target hosts' input ports. The canvas shell parents the item into a view slot; the
item survives slot rebuilds (the slot is destroyed and recreated, the item re-attached).

A **host factory** makes hosts for one view type. Other builders (gallery, Monaco JSON,
video, map) plug in through :func:`specto_app.hosts.register_factory` or the
``specto.view_hosts`` entry-point group; see ``hosts/registry.py``.

``HostContext`` is what a host gets: the engine (``live`` document, ``links`` runtime),
the QML engine for creating items, the runtime health table, and human gestures as
submits (``specto_core.gestures``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError
from PySide6.QtCore import QObject, QPointF, QUrl, Signal, Slot
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem

from specto_core import CallContext, Engine
from specto_core.errors import InvalidArgs, NotFound, SpectoError
from specto_core.kinds import PluginManifest, ViewManifest
from specto_core.links import (
    CameraValue,
    Collection,
    ContentValue,
    FilesValue,
    LinkRuntime,
    Selection,
)
from specto_core.live_canvas import LiveCanvas
from specto_core.ports import VIEW_ROLES

from ..drag.payload import DragPayload, payload_for
from ..menu.registry import MenuItemRef, MenuTarget

if TYPE_CHECKING:
    from specto_app.notices import Notices

QML_DIR = Path(__file__).resolve().parent.parent / "qml"
SHELL_QML = QML_DIR / "Specto" / "Shell"

USER = "user"
OK_HEALTH = {"status": "ok", "message": "", "hint": ""}


class HostContext:
    """Services a host may use. One per shell; shared by its hosts."""

    def __init__(
        self,
        engine: Engine,
        qml_engine: QQmlEngine | None,
        *,
        notices: Notices | None = None,
        origin: str = USER,
        connection: str = "gui",
    ) -> None:
        self.engine = engine
        self.qml_engine = qml_engine
        self.notices = notices
        self.origin = origin
        self.connection = connection
        #: runtime view health (never in the document): ``{view id: {status, message,
        #: hint}}``; ``hint`` is the empty-state line the slot shows in the view's area
        #: ("Pick files in the file tree"), set by the registry, blank otherwise
        self.health: dict[str, dict[str, str]] = {}
        self.health_listeners: list[Callable[[str], None]] = []
        #: the views the user has selected on the canvas, newest first (the shell sets
        #: this; headless: none): a file opened from the file tree reuses a selected preview
        self.selected_views: Callable[[], list[str]] = list
        #: bring a view to the user (#43: a file whose dedicated view is open already):
        #: the shell selects it and pans it into view; headless: nothing
        self.reveal_view: Callable[[str], None] = lambda view_id: None
        #: the shell's context menu (``specto_app.menu.shell.ContextMenus``; headless:
        #: none, so a right-click opens nothing)
        self.context_menus: Any = None
        # shiboken treats items a QQmlComponent created as owned by the component's
        # Python wrapper: when it is collected, the items are invalidated. Keep them.
        self._components: dict[str, QQmlComponent] = {}

    # -- document ----------------------------------------------------------------------
    @property
    def live(self) -> LiveCanvas:
        return self.engine.live

    @property
    def links(self) -> LinkRuntime:
        return self.engine.links

    @property
    def project_root(self) -> Path | None:
        return self.engine.project.root if self.engine.project is not None else None

    def view(self, view_id: str) -> dict[str, Any]:
        """The live view entry (read only; never cache it across patches)."""
        view = self.live.data["views"].get(view_id)
        if view is None:
            raise NotFound(f"no view {view_id!r} on the canvas", fix="state lists the views")
        return view

    def manifest_for(self, view_type: str) -> ViewManifest:
        m = self.engine.plugins.view(view_type)
        return m or ViewManifest(type=view_type, label=view_type, host="quick")

    # -- ports -------------------------------------------------------------------------
    def emit(self, view_id: str, port: str, value: Any) -> None:
        self.links.emit(view_id, port, value)

    # -- gestures (submits as the user) and commands -----------------------------------
    def submit(self, document: dict[str, Any], label: str) -> Any:
        """Submit an edited copy of the document as a user gesture; a ``SpectoError``
        becomes a notice and None."""
        try:
            return self.live.submit(document, origin=self.origin, label=label)
        except SpectoError as e:
            self._notice(e, "submit")
            return None

    def call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a command as the user. Raises ``SpectoError``."""
        return self.engine.call(
            name, dict(args or {}), CallContext(connection=self.connection, origin=self.origin)
        )

    def try_call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any] | None:
        try:
            return self.call(name, args)
        except SpectoError as e:
            self._notice(e, name)
            return None

    def notice(self, e: SpectoError, command: str) -> None:
        """Show a failed gesture or command to the user (a notice, or stderr headless)."""
        self._notice(e, command)

    def _notice(self, e: SpectoError, command: str) -> None:
        if self.notices is not None:
            self.notices.error(e.message, fix=e.fix, code=e.code, command=command)
        else:
            print(f"[ERROR] {command}: {e}")

    # -- context menu (#154) -----------------------------------------------------------
    def open_context_menu(
        self, view_id: str, hit: dict[str, Any] | None, x: float, y: float
    ) -> list[dict[str, Any]]:
        """Open the shell's one context menu for what ``hit`` names in the view, at a
        window point; the rows shown ([] when nothing applies, or headless)."""
        if self.context_menus is None:
            return []
        return self.context_menus.request(view_id, hit, x, y)

    # -- health ------------------------------------------------------------------------
    def set_health(self, view_id: str, status: str, message: str = "", hint: str = "") -> None:
        self.health[view_id] = {"status": status, "message": message, "hint": hint}
        for cb in list(self.health_listeners):
            cb(view_id)

    def health_of(self, view_id: str) -> dict[str, str]:
        return dict(self.health.get(view_id, OK_HEALTH))

    def forget_health(self, view_id: str) -> None:
        if self.health.pop(view_id, None) is not None:
            for cb in list(self.health_listeners):
                cb(view_id)

    # -- items -------------------------------------------------------------------------
    def create_item(
        self,
        source: str | Path | QUrl,
        *,
        properties: dict[str, Any] | None = None,
        parent: QQuickItem | None = None,
    ) -> QQuickItem:
        """Instantiate a QML file as an item. ``source`` is a path, a file URL, or a bare
        file name resolved inside the shell's QML module directory."""
        if self.qml_engine is None:
            raise SpectoError("no QML engine in this process (headless)")
        if isinstance(source, QUrl):
            url = source
        else:
            p = Path(source)
            if not p.is_absolute():
                p = SHELL_QML / p
            url = QUrl.fromLocalFile(str(p))
        key = url.toString()
        comp = self._components.get(key)
        if comp is None or comp.isError():
            comp = QQmlComponent(self.qml_engine, url, self.qml_engine)
            if comp.isError():
                raise SpectoError(f"cannot load {url.toString()}: {comp.errorString()}")
            self._components[key] = comp
        item = comp.createWithInitialProperties(dict(properties or {}))
        if item is None:
            raise SpectoError(f"cannot create {url.toString()}: {comp.errorString()}")
        QQmlEngine.setObjectOwnership(item, QQmlEngine.ObjectOwnership.CppOwnership)
        if parent is not None:
            item.setParentItem(parent)
        return item


class QuickViewHost(QObject):
    """Base class for hosts whose view is a Qt Quick item.

    Subclasses create ``self.item`` in ``__init__`` (usually with
    ``ctx.create_item``) and override ``receive``, ``on_patch`` and ``snapshot``.
    ``dispose`` is called when the view is removed from the document or the canvas
    closes.
    """

    healthChanged = Signal(str, str)

    def __init__(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> None:
        super().__init__()
        self.ctx = ctx
        self.view_id = view_id
        self.manifest = manifest
        self.item: QQuickItem | None = None
        self._disposed = False
        self._host_paused = False

    # -- contract -----------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        """A value landed on one of this view's input ports (None: the link feeding the
        port went away). Show it; never emit it back."""

    def on_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        """Ops under ``/views/<view_id>`` were applied; the document is current."""

    def snapshot(self) -> dict[str, Any]:
        """Structured state for ``snap``. Default: the view's health."""
        return {"health": self.ctx.health_of(self.view_id)}

    # -- pause / resume / snapshot image (contracts section 16) ---------------------------
    def pause(self) -> None:
        """The view left the screen (panned out, or the overworld opened): stop per-frame
        work and release GPU memory where the engine allows. Default: the flag only."""
        self._host_paused = True

    def resume(self) -> None:
        self._host_paused = False

    def snapshot_image(self) -> Any:
        """A ``QImage`` of the view as it looks now, or None to let the shell crop it out
        of a window grab (the default)."""
        return None

    @property
    def host_paused(self) -> bool:
        return self._host_paused

    # -- context menu (#154; contracts section 11) ----------------------------------------
    def context_target(self, hit: dict[str, Any] | None) -> MenuTarget | None:
        """What the menu is about: ``hit`` is what the view's QML or page says is under the
        cursor (``{"kind": "background", ...}``), None when the keyboard asks for the
        focused item. A view with items overrides this to name them; the default is the
        view itself (or its background), the hit kept as ``data``."""
        hit = dict(hit or {})
        kind = hit.get("kind")
        return self.menu_target("background" if kind == "background" else "view", data=hit)

    def menu_target(
        self,
        kind: str,
        items: list[MenuItemRef] | tuple[MenuItemRef, ...] = (),
        data: dict[str, Any] | None = None,
    ) -> MenuTarget:
        """A target on this view: its type and role, the project root."""
        root = self.ctx.project_root
        return MenuTarget(
            view_id=self.view_id,
            view_type=self.manifest.type,
            role=VIEW_ROLES.get(self.manifest.type, ""),
            kind=kind,  # type: ignore[arg-type]
            items=tuple(items),
            root=str(root) if root is not None else "",
            data=dict(data or {}),
        )

    def context_anchor(self, target: MenuTarget) -> tuple[float, float] | None:
        """Where a menu opened from the keyboard goes, in window coordinates: near the
        target. Default: just inside the view's top-left corner."""
        if self.item is None:
            return None
        p = self.item.mapToScene(QPointF(16, 16))
        return p.x(), p.y()

    def open_context_menu(
        self, hit: dict[str, Any] | None, x: float, y: float
    ) -> list[dict[str, Any]]:
        return self.ctx.open_context_menu(self.view_id, hit, x, y)

    # -- drag out (#185; contracts section 11) ----------------------------------------------
    def drag_payload(self, hit: dict[str, Any] | None) -> DragPayload | None:
        """What a drag from ``hit`` carries: by default what the context menu would act
        on there (``context_target``), when that names items; None: nothing to drag."""
        target = self.context_target(hit)
        if target is None or not target.items:
            return None
        return payload_for(target)

    def start_drag(
        self, hit: dict[str, Any] | None, payload: DragPayload | None = None
    ) -> DragPayload | None:
        """The view's pointer left the drag distance with the left button held on
        ``hit``: drag its payload (or ``payload``) out. Returns what was dragged."""
        try:
            payload = payload if payload is not None else self.drag_payload(hit)
            if payload is None or self.item is None:
                return None
            from specto_app.drag.qt import start_drag

            start_drag(self.item, payload)
        except SpectoError as e:
            self.ctx.notice(e, f"drag from {self.view_id}")
            return None
        except Exception as e:  # a broken view never takes the shell down
            self.ctx.notice(SpectoError(f"{type(e).__name__}: {e}"), f"drag from {self.view_id}")
            return None
        return payload

    # -- helpers ------------------------------------------------------------------------
    @property
    def view(self) -> dict[str, Any]:
        return self.ctx.view(self.view_id)

    @property
    def params(self) -> dict[str, Any]:
        return self.view.get("params", {})

    def emit(self, port: str, value: Any) -> None:
        self.ctx.emit(self.view_id, port, value)

    def set_health(self, status: str, message: str = "") -> None:
        self.ctx.set_health(self.view_id, status, message)
        self.healthChanged.emit(status, message)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self.item is not None:
            self.item.setParentItem(None)
            self.item.deleteLater()
            self.item = None

    @property
    def disposed(self) -> bool:
        return self._disposed


@runtime_checkable
class ViewHostFactory(Protocol):
    """Makes hosts for one view type. ``plugin`` (optional) is registered with the
    engine's plugin registry when its view types are not already known."""

    type: str

    def create(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> QuickViewHost: ...


class SimpleFactory:
    """A factory from a callable: ``SimpleFactory("stub", StubHost, plugin=...)``."""

    def __init__(
        self,
        type: str,
        make: Callable[[HostContext, str, ViewManifest], QuickViewHost],
        *,
        plugin: PluginManifest | None = None,
    ) -> None:
        self.type = type
        self._make = make
        self.plugin = plugin

    def create(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> QuickViewHost:
        return self._make(ctx, view_id, manifest)


class HostBridge(QObject):
    """A QObject handed to a host's QML item as ``host`` so QML can call back into
    Python without knowing the host class: ``host.emit("selection", {...})``,
    ``host.setHealth(...)``, ``host.call(name, args)``."""

    def __init__(self, host: QuickViewHost) -> None:
        super().__init__()
        self._host = host

    @Slot(str, "QVariant")
    def emit(self, port: str, value: Any) -> None:
        self._host.emit(port, port_value(port, _plain(value)))

    @Slot(str, str)
    def setHealth(self, status: str, message: str = "") -> None:
        self._host.set_health(status, message)

    @Slot(str, "QVariant", result="QVariant")
    def call(self, name: str, args: Any) -> Any:
        return self._host.ctx.try_call(name, _plain(args) or {})

    @Slot("QVariant", float, float)
    def contextMenu(self, hit: Any, x: float, y: float) -> None:
        """Right-click at a window point: ``hit`` is what is under it (``{kind, ...}``)."""
        self._host.open_context_menu(_plain(hit) or None, float(x), float(y))


def port_value(port: str, value: Any) -> Any:
    """Plain data from QML or a web page as the port's value model where one exists;
    a value the model refuses is an ``InvalidArgs`` error naming the port."""
    if not isinstance(value, dict):
        return value
    model: type[BaseModel] | None = None
    if port == "selection" or set(value) <= {"keys", "last"}:
        model = Selection
    elif port == "camera" or {"position", "target"} <= set(value):
        model = CameraValue
    elif port in ("content", "features") or ("entries" in value and "root" in value):
        model = ContentValue
    elif set(value) == {"items", "root"} or ("items" in value and "generation" in value):
        model = Collection
    elif "root" in value and "paths" in value:
        model = FilesValue
    if model is None:
        return value
    try:
        return model.model_validate(value)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(x) for x in first.get("loc", ())) or port
        raise InvalidArgs(
            f"{port}: {where}: {first.get('msg', 'invalid value')}",
            fix=f"emit a {model.__name__} on {port}",
        ) from None


def _plain(value: Any) -> Any:
    """QML ``var`` values arrive as QJSValue/QVariant; make them plain Python."""
    if hasattr(value, "toVariant"):
        value = value.toVariant()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def dumps(value: Any) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False, default=str)


__all__ = [
    "QML_DIR",
    "SHELL_QML",
    "HostBridge",
    "HostContext",
    "QuickViewHost",
    "SimpleFactory",
    "ViewHostFactory",
    "dumps",
    "port_value",
]
