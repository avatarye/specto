"""Web views: ``WebView`` (one page in one ``WebEngineView``) and ``WebViewHost``
(the ``QuickViewHost`` for ``host="web"`` view types; contracts section 8, ADR 0014).

A ``WebView`` is identified by its ``page_id``, which is also its Chromium origin
(``specto://<page_id>.page``). It owns the capability list and the ``PageBridge``,
and a latest-per-port queue: input values the host receives are queued and flushed once
on the next event-loop turn, so the page sees one bridge message per port however many
arrived (and nothing before it is ready).

``WebViewHost`` wraps one ``WebView`` for a document view (``json``, ``markdown``,
``map``, the 3D view) and plugs into ``specto_app.hosts`` like any Quick host: its
grants follow the links feeding the view, ``receive`` forwards input ports to the page
(subclasses translate what the page should rather see), ``on_patch`` forwards params
and label changes, and the page writes its own params back as user submits. A
``WebView`` can also stand alone, without a document view, for a shell page.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from specto_core.errors import SpectoError
from specto_core.kinds import ViewManifest

from ..drag import DragPayload, payload_for
from ..hosts.base import HostContext, QuickViewHost, port_value
from ..menu import MenuItemRef
from ..theme import theme_for_web
from .assets import monaco_ok, require_monaco, require_vendor
from .capabilities import Capabilities, CapabilityDenied, default_capabilities
from .runtime import runtime
from .scheme import page_url

if TYPE_CHECKING:
    from PySide6.QtQuick import QQuickItem

    from .bridge import PageBridge

WEB_QML = Path(__file__).resolve().parent.parent / "qml" / "Specto" / "Web"


def _have_event_loop() -> bool:
    try:
        from PySide6.QtCore import QCoreApplication

        return QCoreApplication.instance() is not None
    except ImportError:  # pragma: no cover
        return False


def plain(value: Any) -> Any:
    """A port value as JSON-ready data for the page."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def links_into(live_data: dict[str, Any], view_id: str) -> dict[str, dict[str, Any]]:
    """``{port: {link, kind, from}}`` for the links feeding ``view_id``."""
    out: dict[str, dict[str, Any]] = {}
    for lid, link in live_data["links"].items():
        tgt = link["target"]
        if tgt["view"] == view_id:
            src = link["source"]
            out[tgt["port"]] = {
                "link": lid,
                "kind": link["kind"],
                "from": f"{src['view']}.{src['port']}",
            }
    return out


class WebView:
    """One page in one web view. Qt is touched only in ``create_item`` and the bridge."""

    def __init__(
        self,
        ctx: HostContext,
        page_id: str,
        page: str,
        caps: Capabilities,
        *,
        view_id: str | None = None,
        kind: str = "page",
        label: str = "",
    ) -> None:
        self.ctx = ctx
        self.page_id = page_id
        self.page = page
        self.caps = caps
        self.view_id = view_id
        self.kind = kind
        self.label = label
        self.item: QQuickItem | None = None
        self.health = {"status": "", "message": ""}  # unknown until the page reports
        self.page_state: dict[str, Any] = {}  # the page's latest ``Specto.report(...)``
        self.on_ready: Callable[[], None] | None = None
        self.on_health: Callable[[str, str], None] | None = None
        self.on_event: Callable[[str, Any], None] | None = None
        self._pending_ports: dict[str, Any] = {}
        self._pending_params: dict[str, Any] | None = None
        self._pending_notify: dict[str, Any] = {}
        self._flush_scheduled = False
        self._disposed = False
        self._bridge: PageBridge | None = None
        runtime().register(self)

    # -- identity ------------------------------------------------------------------------
    @property
    def engine(self):
        return self.ctx.engine

    @property
    def url(self) -> str:
        return page_url(self.page_id, self.page)

    @property
    def bridge(self) -> PageBridge:
        if self._bridge is None:
            from .bridge import PageBridge

            self._bridge = PageBridge(self)
        return self._bridge

    @property
    def ready(self) -> bool:
        return bool(self._bridge is not None and self._bridge.ready_seen)

    def view(self) -> dict[str, Any] | None:
        if self.view_id is None:
            return None
        try:
            return self.ctx.view(self.view_id)
        except SpectoError:
            return None

    @property
    def params(self) -> dict[str, Any]:
        view = self.view()
        return dict(view.get("params", {})) if view else {}

    def inputs(self) -> dict[str, dict[str, Any]]:
        if self.view_id is None:
            return {}
        return links_into(self.ctx.live.data, self.view_id)

    def config(self) -> dict[str, Any]:
        """What the page gets once after ``ready()``."""
        view = self.view()
        if view is not None:
            self.label = view.get("label", self.label)
        return {
            "page": self.page_id,
            "view": self.view_id,
            "type": self.kind,
            "label": self.label,
            "params": self.params,
            "inputs": self.inputs(),
            "capabilities": list(self.caps),
            "theme": theme_for_web(),
            "dragDistance": drag_distance(),
        }

    # -- page -> core (called by the bridge; capabilities checked here) ------------------
    def emit(self, port: str, value: Any) -> dict[str, Any]:
        self.caps.require("emit", port, page=self.page_id)
        if self.view_id is None:
            raise CapabilityDenied(
                f"page {self.page_id} is not a view; it cannot emit {port}",
                fix="only document views have output ports",
            )
        links = self.ctx.links.emit(self.view_id, port, port_value(port, value))
        return {"links": links}

    def set_params(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Write keys of the view's own ``params`` as one user submit (one undo step)."""
        for key in patch:
            self.caps.require("params", str(key), page=self.page_id)
        if self.view_id is None:
            raise CapabilityDenied(
                f"page {self.page_id} is not a view; it has no params",
                fix="only document views have params",
            )
        doc = self.ctx.live.document()
        view = doc["views"].get(self.view_id)
        if view is None:
            raise CapabilityDenied(f"view {self.view_id} is no longer on the canvas")
        view.setdefault("params", {}).update(patch)
        label = f"{self.label or self.view_id}: {', '.join(str(k) for k in patch)}"
        res = self.ctx.live.submit(doc, origin=self.ctx.origin, label=label)
        return res.model_dump(mode="json")

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.caps.require("cmd", name, page=self.page_id)
        return self.ctx.call(name, args)

    def page_event(self, kind: str, payload: Any) -> None:
        if self.on_event is not None:
            self.on_event(kind, payload)

    # -- core -> page ---------------------------------------------------------------------
    def receive_port(self, port: str, value: Any) -> None:
        """Queue an input port's value (latest per port) for the page."""
        self._pending_ports[port] = plain(value)
        self._schedule_flush()

    def notify(self, kind: str, payload: Any) -> None:
        """Queue a view-specific message (latest per kind), delivered with the next flush."""
        self._pending_notify[kind] = payload
        self._schedule_flush()

    def send_params(self, params: dict[str, Any]) -> None:
        self._pending_params = dict(params)
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        if self._flush_scheduled or self._disposed or not _have_event_loop():
            return
        from PySide6.QtCore import QTimer

        self._flush_scheduled = True
        QTimer.singleShot(0, self._flush_later)

    def _flush_later(self) -> None:
        self._flush_scheduled = False
        self.flush()

    def flush(self) -> int:
        """Send pending port values (one per port), params and notifications. Returns the
        number of messages sent; nothing goes out before the page is ready (the latest
        values wait for it)."""
        if not self.ready:
            return 0
        n = 0
        for port, value in list(self._pending_ports.items()):
            self.bridge.send_port(port, value)
            n += 1
        self._pending_ports.clear()
        if self._pending_params is not None:
            self.bridge.send_params(self._pending_params)
            self._pending_params = None
            n += 1
        for kind, payload in list(self._pending_notify.items()):
            self.bridge.send_notify(kind, payload)
            n += 1
        self._pending_notify.clear()
        return n

    def pending(self) -> dict[str, Any]:
        return {
            "ports": dict(self._pending_ports),
            "params": self._pending_params,
            "notify": dict(self._pending_notify),
        }

    # -- health ---------------------------------------------------------------------------
    def set_health(self, status: str, message: str = "") -> None:
        if self.health == {"status": status, "message": message}:
            return
        self.health = {"status": status, "message": message}
        if self.on_health is not None:
            self.on_health(status, message)
        elif status == "error":
            print(f"[ERROR] page {self.page_id}: {message}", file=sys.stderr, flush=True)

    def _ready(self) -> None:
        """The page connected its bridge (called by ``PageBridge.ready``): the page is
        up, so a ``loading`` from the load start is over unless the host says otherwise."""
        if self.health["status"] in ("", "loading"):
            self.set_health("ok", "")
        if self.on_ready is not None:
            self.on_ready()
        self.flush()

    # -- Qt item ----------------------------------------------------------------------------
    def create_item(
        self,
        parent: QQuickItem | None = None,
        *,
        vendor: tuple[str, ...] = (),
        monaco: bool = True,
    ) -> QQuickItem:
        """Instantiate ``Specto/Web/WebView.qml`` with this page's bridge and URL.
        ``vendor`` lists the ``web/vendor`` files the page needs (checked first); a page
        with none needs Monaco unless ``monaco`` is False."""
        from PySide6.QtCore import QUrl

        if vendor:
            require_vendor(*vendor)  # fail loud before Chromium does
        elif monaco:
            require_monaco()
        self.item = self.ctx.create_item(
            WEB_QML / "WebView.qml",
            properties={"bridge": self.bridge, "source": QUrl(self.url), "pageId": self.page_id},
            parent=parent,
        )
        return self.item

    def dispose(self) -> None:
        """Unregister; the owner deletes the item."""
        if self._disposed:
            return
        self._disposed = True
        runtime().unregister(self)
        self._pending_ports.clear()
        self._pending_notify.clear()
        self._pending_params = None

    @property
    def disposed(self) -> bool:
        return self._disposed


class WebViewHost(QuickViewHost):
    """Base host for ``host="web"`` view types. Subclasses set ``page`` and override
    ``receive`` (what the page should see), ``on_ready``, ``on_params_changed``,
    ``on_page_event`` and ``snapshot``."""

    page: str = "index.html"
    #: a shell QML file (``qml/Specto/Shell``) to show instead of the page when Monaco is
    #: not vendored; ``None`` means the view cannot work without it (health ``error``).
    fallback_qml: str | None = None
    #: ``web/vendor`` files the page needs; empty means Monaco (the M1 pages) unless
    #: ``needs_monaco`` is False (a page with no vendored library at all)
    vendor_files: tuple[str, ...] = ()
    needs_monaco: bool = True

    def __init__(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> None:
        super().__init__(ctx, view_id, manifest)
        self.plain = False  # True when the fallback item is shown
        view = self.view
        self.page = WebView(
            ctx,
            page_id=view_id,
            page=self.page,
            caps=self._grants(),
            view_id=view_id,
            kind=manifest.type,
            label=view.get("label", ""),
        )
        self.page.on_ready = self.on_ready
        self.page.on_health = self._page_health
        self.page.on_event = self._page_event
        runtime().ensure_links(ctx.engine)
        if ctx.qml_engine is not None:
            if (
                self.vendor_files
                or not self.needs_monaco
                or monaco_ok()
                or self.fallback_qml is None
            ):
                self.item = self.page.create_item(
                    vendor=self.vendor_files, monaco=self.needs_monaco
                )
            else:
                warn_no_monaco()
                self.item = ctx.create_item(self.fallback_qml)
                self.plain = True

    # -- plumbing --------------------------------------------------------------------------
    @property
    def caps(self) -> Capabilities:
        return self.page.caps

    @property
    def bridge(self) -> PageBridge:
        return self.page.bridge

    def links_in(self) -> dict[str, str]:
        """``{port: link id}`` for the links feeding this view, from the live document."""
        return {p: d["link"] for p, d in links_into(self.ctx.live.data, self.view_id).items()}

    def _grants(self) -> Capabilities:
        try:
            params = self.view.get("params", {})
        except SpectoError:
            params = {}
        return default_capabilities(self.manifest.type, self.links_in(), params)

    def refresh_grants(self) -> None:
        """The links into this view or its params changed (a value arrived, a link went,
        ``network: false``): re-derive what the page may read, emit and fetch."""
        self.page.caps = self._grants()

    def _page_health(self, status: str, message: str) -> None:
        super().set_health(status, message)

    def set_health(self, status: str, message: str = "") -> None:
        self.page.set_health(status, message)  # routes back through _page_health once

    def notify(self, kind: str, payload: Any) -> None:
        self.page.notify(kind, payload)

    def flush(self) -> int:
        return self.page.flush()

    # -- ViewHost ---------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        """Default: the page gets the port value itself (latest per port). Hosts that
        translate (the json view sends an item URL, the map its layers) override this
        and call ``refresh_grants`` themselves."""
        self.refresh_grants()
        self.page.receive_port(port, value)

    def on_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        prefix = f"/views/{self.view_id}"
        try:
            view = self.view
        except SpectoError:
            return
        paths = [str(op.get("path", "")) for op in patch]
        whole = any(p == prefix for p in paths)
        if whole or any(p.startswith(prefix + "/params") for p in paths):
            params = dict(view.get("params", {}))
            self.refresh_grants()
            self.page.send_params(params)
            self.on_params_changed(params)
        if whole or any(p == prefix + "/label" for p in paths):
            self.page.label = view.get("label", "")
            self.notify("label", self.page.label)

    def snapshot(self) -> dict[str, Any]:
        return {
            **super().snapshot(),
            "type": self.manifest.type,
            "url": self.page.url,
            "capabilities": list(self.caps),
            "inputs": self.links_in(),
            "params": self.page.params,
            "ready": self.page.ready,
            "page": dict(self.page.page_state),
        }

    def _page_event(self, kind: str, payload: Any) -> None:
        if kind == "contextmenu":
            self.page_context_menu(payload)
        elif kind == "drag":
            from PySide6.QtCore import QTimer

            # not inside the page's message: QDrag.exec is a nested loop, and run from a
            # Chromium task it starves Chromium's own (non-nestable) tasks, so the web
            # view under the pointer never answers the drag (Linux: "updateDragAction was
            # not called within 3000 ms", no drop); from the Qt loop the drag is plain
            hit = payload if isinstance(payload, dict) else None
            QTimer.singleShot(0, lambda: self.start_drag(hit))
        else:
            self.on_page_event(kind, payload)

    # -- drag out (#185) ------------------------------------------------------------------------
    def drag_payload(self, hit: dict[str, Any] | None) -> DragPayload | None:
        """The page's ``drag {text, html}`` (``web/bridge.js``: a dragged text selection):
        a ``text`` item from the file the view shows (``text_source``), the text and its
        html; no file URL (an outside app takes the text, not the file)."""
        text = str((hit or {}).get("text") or "")
        if not text:
            return None
        html = (hit or {}).get("html")
        ref = self.text_source()
        target = self.menu_target("items" if ref else "view", [ref] if ref else [])
        labels = {ref.key: self.text_label(ref)} if ref else None
        return payload_for(
            target,
            kind="text",
            labels=labels,
            text=text,
            html=html if isinstance(html, str) and html else None,
        )

    def text_source(self) -> MenuItemRef | None:
        """The item a dragged text comes from (key and file); None: the view's own."""
        return None

    def text_label(self, ref: MenuItemRef) -> str:
        return Path(ref.path.partition("#")[0]).name or ref.key

    def page_context_menu(self, payload: Any) -> list[dict[str, Any]]:
        """The page's ``contextmenu {x, y, target}`` (#154; ``web/bridge.js`` sends it for a
        right-click, the Menu key or Shift+F10 the page did not handle): (x, y) are CSS px
        in the page, ``target`` what the page says is there (``{kind, ...}``; none: its
        background). Opens the shell's menu at that window point."""
        p = payload if isinstance(payload, dict) else {}
        target = p.get("target")
        hit = dict(target) if isinstance(target, dict) else {"kind": "background"}
        try:
            x, y = float(p.get("x", 0)), float(p.get("y", 0))
        except (TypeError, ValueError):
            x = y = 0.0
        if self.item is not None:
            from PySide6.QtCore import QPointF

            pt = self.item.mapToScene(QPointF(x, y))
            x, y = pt.x(), pt.y()
        return self.open_context_menu(hit, x, y)

    # -- hooks ---------------------------------------------------------------------------------
    def on_ready(self) -> None:
        """The page connected its bridge; send whatever it should show now."""

    def on_params_changed(self, params: dict[str, Any]) -> None:
        """The view's params changed in the document (the page already gets them)."""

    def on_page_event(self, kind: str, payload: Any) -> None:
        """The page sent something that is not an emit or a call (``Specto.send``)."""

    def dispose(self) -> None:
        self.page.dispose()
        if self.item is not None and not item_alive(self.item):
            self.item = None  # the window already deleted it (shutdown order)
        super().dispose()


def drag_distance() -> int:
    """The platform's drag distance (px), for pages that detect their own drags."""
    try:
        from PySide6.QtGui import QGuiApplication

        return int(QGuiApplication.styleHints().startDragDistance())
    except Exception:  # no application (headless tools)
        return 10


_warned_no_monaco = False


def warn_no_monaco() -> None:
    global _warned_no_monaco
    if not _warned_no_monaco:
        _warned_no_monaco = True
        from specto_web import BUILD_HINT

        print(f"[WARN] Monaco is not vendored; web views fall back to plain text. {BUILD_HINT}")


def item_alive(item: Any) -> bool:
    """False when the C++ side of a Qt object is gone (shutdown order)."""
    try:
        import shiboken6

        return bool(shiboken6.isValid(item))
    except ImportError:  # pragma: no cover
        return True


__all__ = [
    "WEB_QML",
    "WebView",
    "WebViewHost",
    "item_alive",
    "links_into",
    "plain",
    "warn_no_monaco",
]
