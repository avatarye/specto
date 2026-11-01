"""The ``pdf`` view host (#69; ports per ADR 0014): the pages of the selected pdf of its
collection, rendered by QtPdf.

One :class:`PdfHost` per live pdf view. It owns ``Specto/Pdf/PdfView.qml``, a
``PdfMultiPageView`` from Qt's ``QtQuick.Pdf`` module (pages rendered off the GUI
thread by QtPdf's PDFium, no web view, nothing vendored) under a footer with the page
counter and the controls. Ports:

- ``data`` in: the collection its intaker resolved; the items with a ``.pdf`` part are
  the documents, the first shows until a selection says otherwise;
- ``selection`` in: the document to show (the last selected key); shown, never emitted.

Previous / next (``hosts.stepping``; Left / Right and the footer's buttons) step the
documents; PageUp / PageDown, Space and Backspace keep turning the pages, as in every
pdf reader. Nothing goes out: pages, zoom and fit are the view's own, reported by ``snapshot()``
with the key, index and page count. The host loads each document once more in its own
``QPdfDocument`` for the page count and the health line, so a headless host (tests,
``snap``) knows them without the item. Health: ``ok`` with the name and page count,
``error`` when QtPdf cannot open the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtPdf import QPdfDocument

from specto_app.hosts.base import HostContext, QuickViewHost
from specto_app.hosts.stepping import Stepping
from specto_core.kinds import Item, PluginManifest, ViewManifest
from specto_core.links import Collection, Selection

DATA, SELECTION = "data", "selection"
PDF_ROLES = (".pdf",)
PDF_QML = Path(__file__).resolve().parent.parent / "qml" / "Specto" / "Pdf" / "PdfView.qml"
FITS = ("width", "page", "none")

PDF_MANIFEST = ViewManifest(
    type="pdf",
    label="PDF",
    host="quick",
    doc="Pages of the selected pdf of its collection (or the first), rendered by QtPdf: "
    "scroll or page through, zoom with Ctrl+wheel or +/-, fit width (F) or fit page (P), "
    "a page counter; shows the selection it receives and emits nothing.",
    accepts=["pdf"],
    example={
        "view": {"type": "pdf", "label": "Report"},
        "link": {
            "kind": "intaker",
            "scope": "selection",
            "source": {"view": "files", "port": "selected_files"},
            "target": {"view": "duna", "port": "data"},
            "params": {"screener": ["@pdf"]},
            "summary": {"sentence": "The pdf view shows the pdf picked in the file tree."},
        },
    },
)
PDF_PLUGIN = PluginManifest(name="app-pdf", version="0.1.0", views=[PDF_MANIFEST])


class PdfBridge(QObject):
    """Handed to ``PdfView.qml`` as ``bridge``."""

    def __init__(self, host: PdfHost) -> None:
        super().__init__()
        self._host = host

    @Slot(int)
    def step(self, delta: int) -> None:
        self._host.step_item(int(delta))

    @Slot(int, float, str)
    def viewChanged(self, page: int, zoom: float, fit: str) -> None:
        self._host.on_view_changed(int(page), float(zoom), str(fit))


class PdfHost(Stepping, QuickViewHost):
    """One live pdf view; see the module docstring."""

    def __init__(
        self,
        ctx: HostContext,
        view_id: str,
        manifest: ViewManifest,
        *,
        headless: bool = False,
    ) -> None:
        super().__init__(ctx, view_id, manifest)
        self.collection: Collection | None = None
        self.items: list[Item] = []  # the items with a pdf part, in collection order
        self.keys: list[str] = []
        self.paths: list[str] = []
        self._index_of: dict[str, int] = {}
        self._wanted_key: str | None = None  # the last selection received
        self.index = -1
        self.page = 0
        self.page_count = 0
        self.zoom = 1.0
        self.fit = "width"
        self.document_status = "empty"
        self._document = QPdfDocument()
        self.bridge = PdfBridge(self)
        if ctx.qml_engine is not None and not headless:
            self.item = ctx.create_item(
                PDF_QML, properties={"viewId": view_id, "bridge": self.bridge}
            )
        self.set_health("paused", "no data")

    # -- ports -------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        if port == DATA:
            self.collection = value if isinstance(value, Collection) else None
            self.items, self.paths = [], []
            for it in self.collection.items if self.collection else []:
                role = next((r for r in PDF_ROLES if r in it.parts), None)
                if role is not None:
                    self.items.append(it)
                    self.paths.append(it.parts[role])
            self.keys = [it.key for it in self.items]
            self._index_of = {k: i for i, k in enumerate(self.keys)}
            key = self.key
            want = self._wanted_key if self._wanted_key in self._index_of else key
            i = self._index_of.get(want, -1) if want is not None else -1
            if i < 0 and self.items:
                i = 0
            self._show(i)
            if not self.items:
                self.set_health("paused", "no pdf" if self.collection else "no data")
        elif port == SELECTION:
            key = value.picked if isinstance(value, Selection) else None
            self._wanted_key = key
            if key is not None:
                self.show_key(key)

    # -- items -------------------------------------------------------------------------
    def index_of(self, key: str) -> int:
        return self._index_of.get(key, -1)

    @property
    def key(self) -> str | None:
        return self.keys[self.index] if 0 <= self.index < len(self.keys) else None

    @property
    def path(self) -> str:
        return self.paths[self.index] if 0 <= self.index < len(self.paths) else ""

    def caption_of(self, index: int) -> str:
        if index < 0 or index >= len(self.items):
            return f"{len(self.items)} documents" if self.items else ""
        return Path(self.paths[index]).name  # the footer shows n / N (#126)

    # -- showing -----------------------------------------------------------------------
    def _show(self, index: int) -> None:
        self.index = index if 0 <= index < len(self.items) else -1
        path = self.path
        self.page = 0
        if path:
            err = self._document.load(path)
            ok = err == QPdfDocument.Error.None_
            self.page_count = self._document.pageCount() if ok else 0
            self.document_status = "ok" if ok else "error"
        else:
            self._document.close()
            self.page_count = 0
            self.document_status = "empty"
        if self.item is not None:
            self.item.setProperty("caption", self.caption_of(self.index))
            self.item.setProperty("source", QUrl.fromLocalFile(path) if path else QUrl())
        self.sync_step()
        if self.index >= 0:
            name = Path(path).name
            if self.document_status == "ok":
                pages = "1 page" if self.page_count == 1 else f"{self.page_count} pages"
                self.set_health("ok", f"{name}  {pages}")
            else:
                self.set_health("error", f"cannot open {name}")

    def show_key(self, key: str) -> int:
        """Show ``key`` (a selection from outside); -1 when it is not a pdf here."""
        i = self.index_of(key)
        if i >= 0 and i != self.index:
            self._show(i)
        return i

    # -- stepping (hosts.stepping) ------------------------------------------------------
    def step_keys(self) -> list[str]:
        return self.keys

    def step_key(self) -> str | None:
        return self.key

    def show_step(self, key: str) -> bool:
        self._wanted_key = key
        return self.show_key(key) >= 0

    # -- the item's own navigation -------------------------------------------------------
    def on_view_changed(self, page: int, zoom: float, fit: str) -> None:
        """The item scrolled to another page, zoomed or fitted: remember it for ``snap``."""
        self.page, self.zoom, self.fit = page, zoom, fit if fit in FITS else "none"

    # -- contract ----------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        page, zoom, fit = self.page, self.zoom, self.fit
        if self.item is not None:
            page = int(self.item.property("currentPage") or 0)
            zoom = float(self.item.property("zoom") or zoom)
            fit = str(self.item.property("fit") or fit)
        return {
            **super().snapshot(),
            "count": len(self.items),
            "selected": {"index": self.index, "key": self.key},
            "page": page,
            "page_count": self.page_count,
            "zoom": zoom,
            "fit": fit,
            "document": {"status": self.document_status, "path": self.path},
        }

    def dispose(self) -> None:
        super().dispose()
        self._document.close()


class PdfFactory:
    type = "pdf"
    plugin = PDF_PLUGIN

    def create(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> PdfHost:
        return PdfHost(ctx, view_id, manifest)


def factory() -> PdfFactory:
    return PdfFactory()


def register(registry: Any) -> None:
    """Entry point / ``--host-module`` hook: ``registry.add_factory(PdfFactory())``."""
    registry.add_factory(factory())


__all__ = [
    "DATA",
    "PDF_MANIFEST",
    "PDF_PLUGIN",
    "PDF_QML",
    "PDF_ROLES",
    "SELECTION",
    "PdfBridge",
    "PdfFactory",
    "PdfHost",
    "factory",
    "register",
]
