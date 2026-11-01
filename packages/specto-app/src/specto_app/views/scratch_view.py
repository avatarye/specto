"""The ``scratch`` view host (#184): the project's scratch pad, named pages of markdown.

One :class:`ScratchHost` per live scratch view (a canvas holds at most one). It shows the
engine's ``ScratchStore`` (``.specto/scratch``): ``Specto/Scratch/ScratchView.qml`` puts
the pad's tab strip (+ adds a page, a click shows one, double-click renames, a drag moves,
x closes: each an undo step but the click) over the web page ``scratch/index.html``,
which renders the shown page like the markdown view (outline, zoom) and edits it as
source in Monaco (a rendered / source toggle; the source autosaves, one undo step per
save). The store's changes (the user's, the agent's, an undo) go to the page. The page
shown is the store's active page; ``params.page`` (an id or a name; the agent sets it)
chooses it, and a tab click writes it (navigation, no undo step). Ports:

- ``items`` out: the shown page (``ScratchStore.collection``: its markdown, then the
  files and pictures it links), on every change and every page switch;
- ``selection`` out: a file link the user clicks in the page, which the pad's preview
  beside it shows (``gestures.open_from_scratch``); ``selection`` in: ignored.

Drops and Ctrl+V (``accept_mime``) take everything in the drag and drop contract
(``docs/contracts.md`` section 11), from Specto views and from other apps: the QML's drop
area over the page hands the drop's data per mime type to ``ScratchBridge.drop``; a paste
reads the clipboard's ``QMimeData`` (the page asks for it on Ctrl+V). What they hold is
appended to the shown page as markdown (``ScratchStore.markdown_of``: text as text,
pictures as png assets, files as links, a Specto item as a quote with its source), at the
editor's caret while the source shows, or at the end of the page whose tab it was
dropped on. One undo step per drop or paste.

The page reads the note's pictures from ``specto://items/scratch-<view>/<page>/.md/
<page>.assets/<name>``: the host serves its pages under that link id
(``ScratchItems``) and grants its page ``read:link`` on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData, QObject, QPointF, QUrl, Slot
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickItem

from specto_app import clipboard
from specto_app.hosts.base import HostContext, _plain
from specto_app.web.capabilities import Capabilities
from specto_app.web.runtime import runtime
from specto_app.web.scheme import item_url
from specto_app.web.webview import WebViewHost, item_alive
from specto_core.errors import SpectoError
from specto_core.gestures import open_from_scratch
from specto_core.kinds import BUILTIN_VIEWS, PluginManifest, ViewManifest
from specto_core.links import Collection, Selection
from specto_core.scratch import (
    PNG_MIME,
    SPECTO_MIME,
    TEXT_MIME,
    URI_MIME,
    NewItem,
    ScratchStep,
    ScratchStore,
    entries_from_mime,
)

ITEMS, SELECTION = "items", "selection"
SCRATCH_QML = (
    Path(__file__).resolve().parent.parent / "qml" / "Specto" / "Scratch" / "ScratchView.qml"
)
#: the mime types a drop is read for: the contract's, any image, and what a browser
#: offers on Windows for a dragged picture (a virtual file's contents)
DROP_FORMATS = (SPECTO_MIME, PNG_MIME, URI_MIME, TEXT_MIME)

SCRATCH_MANIFEST = next(m for m in BUILTIN_VIEWS if m.type == "scratch")
SCRATCH_PLUGIN = PluginManifest(name="app-scratch", version="0.2.0", views=[SCRATCH_MANIFEST])


def wanted(fmt: str) -> bool:
    """A mime type a drop is read for."""
    return fmt in DROP_FORMATS or fmt.startswith("image/") or "FileContents" in fmt


def to_png(data: bytes) -> bytes | None:
    """Image bytes of any format Qt reads, as png; None when they are no image."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data
    img = QImage()
    if not img.loadFromData(QByteArray(data)):
        return None
    return image_png(img)


def image_png(img: QImage) -> bytes | None:
    if img.isNull():
        return None
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data().data())


def mime_formats(md: QMimeData) -> dict[str, bytes]:
    """A drop's or the clipboard's data as plain bytes per mime type: the contract's
    types (text and URLs through Qt's own conversion, so a Windows clipboard's text is
    UTF-8 here), any image type, and a virtual file's contents when they are a picture
    (``image/x-file-contents``)."""
    out: dict[str, bytes] = {}
    for fmt in md.formats():
        if fmt in (SPECTO_MIME, PNG_MIME) or fmt.startswith("image/"):
            data = bytes(md.data(fmt).data())
            if data:
                out[fmt] = data
        elif "FileContents" in fmt:
            data = bytes(md.data(fmt).data())
            if to_png(data) is not None:
                out["image/x-file-contents"] = data
    if md.hasUrls():
        urls = [u.toString(QUrl.ComponentFormattingOption.FullyEncoded) for u in md.urls()]
        out[URI_MIME] = "\r\n".join(urls).encode("utf-8")
    if md.hasText():
        out[TEXT_MIME] = md.text().encode("utf-8")
    if md.hasImage() and not any(k.startswith("image/") for k in out):
        png = image_png(QImage(md.imageData()))
        if png:
            out[PNG_MIME] = png
    return out


def mime_from(formats: dict[str, Any]) -> QMimeData:
    """A ``QMimeData`` from the QML drop's ``{format: ArrayBuffer | string}``."""
    md = QMimeData()
    for fmt, value in formats.items():
        if isinstance(value, QByteArray):
            data = bytes(value.data())
        elif isinstance(value, bytes | bytearray | memoryview):
            data = bytes(value)
        elif value is None:
            continue
        else:
            data = str(value).encode("utf-8")
        if fmt == URI_MIME:
            md.setUrls([QUrl(line) for line in data.decode("utf-8").split() if line])
        elif fmt == TEXT_MIME:
            md.setText(data.decode("utf-8", errors="replace"))
        else:
            md.setData(fmt, QByteArray(data))
    return md


class ScratchItems:
    """The pad's pages on ``specto://items/<link id>/<page id>/.md``, so its page reads a
    note's pictures next to it (``<page>.assets/<name>``); an ``ItemResolver``."""

    def __init__(self, link_id: str, store: ScratchStore) -> None:
        self.link_id = link_id
        self.store = store

    def parts(self, link_id: str, key: str) -> dict[str, str] | None:
        if link_id != self.link_id or key not in self.store.page_ids():
            return None
        return {".md": str(self.store.md_path(key))}

    def keys(self, link_id: str) -> list[str]:
        return self.store.page_ids() if link_id == self.link_id else []


class ScratchBridge(QObject):
    """Handed to ``ScratchView.qml`` as ``bridge``: the tab strip and the drop area."""

    def __init__(self, host: ScratchHost) -> None:
        super().__init__()
        self._host = host

    @Slot("QVariant", str, result=int)
    def drop(self, formats: Any, page: str = "") -> int:
        """A drop's data (``{mime type: data}``) on the shown page, or on ``page`` (a
        tab dropped on); the number of entries added."""
        data = _plain(formats) or {}
        return len(self._host.accept_mime(mime_from(dict(data)), page=str(page) or None))

    @Slot(result=int)
    def paste(self) -> int:
        return len(self._host.paste())

    @Slot(str)
    def showPage(self, page: str) -> None:
        self._host.show_page(page)

    @Slot(result=str)
    def addPage(self) -> str:
        return self._host.add_page() or ""

    @Slot(str, str)
    def renamePage(self, page: str, name: str) -> None:
        self._host.rename_page(page, name)

    @Slot(str, int)
    def movePage(self, page: str, index: int) -> None:
        self._host.move_page(page, int(index))

    @Slot(str)
    def closePage(self, page: str) -> None:
        self._host.close_page(page)


class ScratchHost(WebViewHost):
    """The scratch pad's view; see the module docstring."""

    page = "scratch/index.html"
    vendor_files = (
        "markdown-it/markdown-it.umd.min.js",
        "github-markdown-css/github-markdown-light.css",
        "mermaid/mermaid.min.js",
        "katex/katex.min.js",
        "vs/loader.js",
    )

    def __init__(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> None:
        self.store: ScratchStore = ctx.engine.scratch
        self.items_link = f"scratch-{view_id}"
        self._resolver = ScratchItems(self.items_link, self.store)
        runtime().add_items(self._resolver)
        super().__init__(ctx, view_id, manifest)
        self.tabs: list[dict[str, Any]] = []
        #: where a drop lands while the source shows (the editor's caret on the shown
        #: page, a character offset); None: drops append at the end
        self.caret: int | None = None
        self.rev = 0
        self.bridge_qml = ScratchBridge(self)
        self._web_item: QQuickItem | None = None
        self._slot: QQuickItem | None = None
        if self.item is not None:
            self._web_item = self.item
            self.item = ctx.create_item(
                SCRATCH_QML, properties={"viewId": view_id, "bridge": self.bridge_qml}
            )
            self._slot = self.item.findChild(QQuickItem, f"scratchSlot:{view_id}")
            if self._slot is not None:
                self._web_item.setParentItem(self._slot)
                self._slot.widthChanged.connect(self._fit)
                self._slot.heightChanged.connect(self._fit)
                self._fit()
        self._off = self.store.on_change(self._changed)
        self._follow_param(self.params)
        self.refresh()

    def _grants(self) -> Capabilities:
        caps = super()._grants()
        return Capabilities.of([*caps, f"read:link:{self.items_link}"])

    @Slot()
    def _fit(self) -> None:
        if self._web_item is not None and self._slot is not None:
            self._web_item.setWidth(self._slot.width())
            self._web_item.setHeight(self._slot.height())

    # -- the store ---------------------------------------------------------------------
    def _changed(self, step: ScratchStep | None, kind: str) -> None:
        if kind != "squash":  # a squash folds steps: nothing to redraw
            self.refresh()

    def refresh(self) -> None:
        """The tabs to the strip, the shown page to the page, its collection out."""
        store = self.store
        pid = store.active
        self.tabs = [
            {"id": p["id"], "name": p["name"], "count": len(store.text(p["id"]))}
            for p in store.pages
        ]
        if self.item is not None:
            self.item.setProperty("tabs", self.tabs)
            self.item.setProperty("page", pid)
        self.rev += 1
        self.send_page()
        name = store.page(pid)["name"]
        chars = len(store.text(pid))
        words = "empty" if not chars else f"{chars} characters"
        self.set_health("ok", f"{name}: {words}" if len(store.pages) > 1 else words)
        col = store.collection(pid)
        self.emit(ITEMS, Collection(items=col.items, root=col.root, generation=self.rev))

    def send_page(self) -> None:
        pid = self.store.active
        self.notify(
            "page",
            {
                "id": pid,
                "name": self.store.page(pid)["name"],
                "url": item_url(self.items_link, pid, ".md"),
                "text": self.store.text(pid),
                "rev": self.rev,
            },
        )

    # -- pages ---------------------------------------------------------------------------
    def _follow_param(self, params: dict[str, Any]) -> None:
        """``params.page`` (the agent's, or a tab click remembered) picks the page shown;
        a name that is no page leaves the shown one and says so."""
        want = params.get("page")
        if not want:
            return
        try:
            self.store.set_active(str(want))
        except SpectoError as e:
            self.ctx.notice(e, "scratch page")

    def on_params_changed(self, params: dict[str, Any]) -> None:
        self._follow_param(params)

    def show_page(self, page: str) -> None:
        """A tab click: show that page and remember it in ``params.page`` (navigation)."""
        if self._do(lambda: self.store.set_active(page), "scratch page") is None:
            return
        pid = self.store.active
        self.caret = None
        if self.params.get("page") != pid:
            self.ctx.live.remember(self.view_id, {"page": pid})

    def add_page(self) -> str | None:
        """The strip's +: a new page, shown (one undo step)."""
        if self._do(lambda: self.store.add_page(origin="user"), "scratch page") is None:
            return None
        self.show_page(self.store.active)
        return self.store.active

    def rename_page(self, page: str, name: str) -> None:
        self._do(lambda: self.store.rename_page(page, name, origin="user"), "scratch page")

    def move_page(self, page: str, index: int) -> None:
        self._do(lambda: self.store.move_page(page, index, origin="user"), "scratch page")

    def close_page(self, page: str) -> None:
        """A tab's x: the page goes (one undo step); a non-empty one says how to bring it
        back."""
        step = self._do(lambda: self.store.remove_page(page, origin="user"), "scratch page")
        if step is None:
            return
        if step.label.endswith("(not empty)") and self.ctx.notices is not None:
            words = step.label.removesuffix(" (not empty)")
            self.ctx.notices.info(f"{words}: Ctrl+Z brings it back")
        if self.params.get("page") not in (None, *self.store.page_ids()):
            self.ctx.live.remember(self.view_id, {"page": self.store.active})

    # -- the page's events ---------------------------------------------------------------
    def on_ready(self) -> None:
        self.send_page()

    def on_page_event(self, kind: str, payload: Any) -> None:
        p = payload if isinstance(payload, dict) else {}
        if kind == "save":
            self.save(str(p.get("page") or ""), str(p.get("text") or ""))
        elif kind == "caret":
            offset = p.get("offset")
            on_shown = p.get("page") == self.store.active
            self.caret = int(offset) if isinstance(offset, int | float) and on_shown else None
        elif kind == "open":
            self.open_link(str(p.get("href") or ""))
        elif kind == "paste":
            self.paste()

    def save(self, page: str, text: str) -> None:
        """The source editor's autosave: the page's whole markdown, one undo step."""
        if page not in self.store.page_ids():
            return
        self._do(lambda: self.store.write(text, page=page, origin="user"), "scratch save")

    def open_link(self, href: str) -> str | None:
        """A file link clicked in the page: it goes out as the selection and the pad's
        preview (opened beside it when missing) shows it; the preview's id."""
        path = self.store.resolve_target(href)
        if path is None or not path.exists():
            if self.ctx.notices is not None:
                self.ctx.notices.info(f"no file behind the link {href}")
            return None
        items = self.store.collection().items[1:]
        key = next((it.key for it in items if Path(next(iter(it.parts.values()))) == path), None)
        if key is None:
            return None
        try:
            vid, _res = open_from_scratch(self.ctx.live, self.view_id, "preview")
        except SpectoError as e:
            self.ctx.notice(e, "scratch open")
            return None
        self.emit(SELECTION, Selection(keys=[key], last=key))
        return vid

    # -- drops and pastes -------------------------------------------------------------------
    def _do(self, fn: Any, what: str) -> Any:
        try:
            return fn()
        except SpectoError as e:
            self.ctx.notice(e, what)
            return None

    def accept_mime(self, md: QMimeData, *, page: str | None = None) -> list[NewItem]:
        """A drop or a paste: appended to the shown page as markdown (at the editor's
        caret while the source shows), or to the end of ``page`` (a tab). One undo step;
        the entries added ([] when nothing in it is usable)."""
        entries: list[NewItem] = []
        for e in entries_from_mime(mime_formats(md)):
            if e.kind == "image" and e.png is not None:
                png = to_png(e.png)
                if png is None:
                    continue
                e.png = png
            entries.append(e)
        if not entries:
            if self.ctx.notices is not None:
                self.ctx.notices.info("nothing in that drop the scratch pad can keep")
            return []
        target = self.store.page(page)["id"] if page else self.store.active
        at = self.caret if target == self.store.active else None

        def add() -> Any:
            text, assets = self.store.markdown_of(entries, target)
            return self.store.append(text, page=target, at=at, assets=assets, origin="user")

        if self._do(add, "scratch drop") is None:
            return []
        return entries

    def paste(self) -> list[NewItem]:
        """Ctrl+V: the clipboard, as a drop on the shown page."""
        md = clipboard.mime_data()
        return self.accept_mime(md) if md is not None else []

    # -- context menu ----------------------------------------------------------------------
    def page_context_menu(self, payload: Any) -> list[dict[str, Any]]:
        """The page's right-click, at its point inside the web item (below the tabs)."""
        p = payload if isinstance(payload, dict) else {}
        target = p.get("target")
        hit = dict(target) if isinstance(target, dict) else {"kind": "background"}
        try:
            x, y = float(p.get("x", 0)), float(p.get("y", 0))
        except (TypeError, ValueError):
            x = y = 0.0
        if self._web_item is not None:
            pt = self._web_item.mapToScene(QPointF(x, y))
            x, y = pt.x(), pt.y()
        return self.open_context_menu(hit, x, y)

    # -- contract ----------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        store = self.store
        snap = super().snapshot()
        if self._slot is not None:  # where the page starts below the header (the tabs)
            snap["page"]["top"] = float(self._slot.y())
        return {
            **snap,
            "pages": [store.page_summary(p) for p in store.pages],
            "shown": store.active,
            "chars": len(store.text()),
            "caret": self.caret,
        }

    def dispose(self) -> None:
        self._off()
        runtime().items.remove(self._resolver)
        web = self._web_item
        self._web_item = None
        super().dispose()
        if web is not None and item_alive(web):
            web.setParentItem(None)
            web.deleteLater()


class ScratchFactory:
    type = "scratch"
    plugin = SCRATCH_PLUGIN

    def create(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> ScratchHost:
        return ScratchHost(ctx, view_id, manifest)


def factory() -> ScratchFactory:
    return ScratchFactory()


def register(registry: Any) -> None:
    """Entry point / ``--host-module`` hook: ``registry.add_factory(ScratchFactory())``."""
    registry.add_factory(factory())


__all__ = [
    "ITEMS",
    "SCRATCH_MANIFEST",
    "SCRATCH_PLUGIN",
    "SCRATCH_QML",
    "SELECTION",
    "ScratchBridge",
    "ScratchFactory",
    "ScratchHost",
    "ScratchItems",
    "factory",
    "mime_formats",
    "mime_from",
    "register",
    "to_png",
    "wanted",
]
