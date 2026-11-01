"""The ``preview`` view host (#106, ADR 0020): the context viewer. It renders the picked
item of its collection (or the first) by its kind, in the renderer of that kind: the
image, pdf, video, markdown, json, html or 3D view's own host, made through that view
type's factory and never copied.

One :class:`PreviewHost` per live preview view. It owns ``Specto/Preview/PreviewView.qml``,
a frame the renderer's item is parented into. Ports:

- ``data`` in: the collection its intaker resolved; each item's kind is the data kind of
  its parts (the locators' kinds by extension, ``RENDERER_BY_KIND``; an item with several
  parts shows the one whose renderer comes first in ``RENDERERS``);
- ``selection`` in: the item to show (the last picked key); shown, not republished;
- ``selection`` out: an item the renderer moved to itself (an image step, a wikilink, a
  file picked in the html view's list), and ``step``.

The renderer gets the items of its own kind as its ``data`` and the shown key as its
``selection``, so it steps and navigates among them as the typed view would; the video
and 3D renderers get the shown item alone (a 3D view would place every item, a video
plays the first). A pick of another kind disposes the renderer (its item, its web page,
its decoder) before the next one is made, like the 3D view's source switch. Renderers
run on a :class:`RendererContext`: the preview's own view id, health and params, with
their emits routed through the preview (only a selection of an item in the collection
goes out; content, time and camera stay inside, a preview has no such ports). An item
that no renderer shows gets a quiet card: its file name, its size and a hint to open it
in its own view.

Stepping (#107, ``hosts.stepping``): the preview steps through its whole collection,
across kinds, and emits the item it steps to; its renderer's controls and keys step the
preview (the renderer's ``step_owner``) and show the preview's place, and the status bar
names the renderer's step keys (``step_type``).
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Slot
from PySide6.QtQuick import QQuickItem

from specto_core.errors import SpectoError
from specto_core.kinds import Item, PluginManifest, ViewCost, ViewManifest
from specto_core.links import Collection, Selection

from ..hosts.base import HostContext, QuickViewHost, ViewHostFactory
from ..hosts.stepping import Stepping
from .channels import CHANNELS_SCHEMA
from .file_icons import icon_for
from .file_tree import format_size

DATA, SELECTION = "data", "selection"
PREVIEW_QML = (
    Path(__file__).resolve().parent.parent / "qml" / "Specto" / "Preview" / "PreviewView.qml"
)

#: data kind -> the view type whose host renders it in a preview (the map takes
#: features, not a collection: a geojson file shows as json)
RENDERER_BY_KIND: dict[str, str] = {
    "image2d": "image",
    "video_ref": "video",
    "pdf": "pdf",
    "pointcloud": "3d",
    "mesh": "3d",
    "splat": "3d",
    "html": "html",
    "markdown": "markdown",
    "json": "json",
    "geojson": "json",
    "text": "json",
}
#: the renderers, first preferred for an item with parts of several kinds (a frame with
#: its json sidecar shows the frame)
RENDERERS: tuple[str, ...] = ("image", "video", "pdf", "3d", "html", "markdown", "json")
#: renderers fed the shown item alone
ONE_ITEM: frozenset[str] = frozenset({"video", "3d"})
OPEN_HINT = "No preview for this kind of file: open it in its own view."

PREVIEW_MANIFEST = ViewManifest(
    type="preview",
    label="Preview",
    host="quick",
    doc="Quick look: the picked item of its collection (or the first) rendered by its "
    "kind with the image, video, pdf, markdown, json, html or 3D view's own renderer; "
    "other files show their name and size. The default for a double-click in the file "
    "tree; typed views stay for compositions that need their ports (a video's time).",
    accepts=sorted(RENDERER_BY_KIND),
    cost=ViewCost(web_views=1),
    state_schema={
        "type": "object",
        "properties": {"channels": CHANNELS_SCHEMA},  # while an image shows (#171)
        "additionalProperties": True,
    },
    example={
        "view": {"type": "preview", "label": "Quick look"},
        "link": {
            "kind": "intaker",
            "scope": "selection",
            "source": {"view": "files", "port": "selected_files"},
            "target": {"view": "pela", "port": "data"},
            "params": {},
            "summary": {"sentence": "The preview shows the file picked in the file tree."},
        },
    },
)
PREVIEW_PLUGIN = PluginManifest(name="app-preview", version="0.1.0", views=[PREVIEW_MANIFEST])


def builtin_renderers() -> dict[str, ViewHostFactory]:
    """The built-in factories of the renderer view types, by type."""
    from . import factories

    return {f.type: f for f in factories() if f.type in RENDERERS}


class RendererContext(HostContext):
    """The context a preview's renderer runs on: the preview's own (same engine, QML
    engine, health table and notices), with emits handed to the preview."""

    def __init__(self, parent: HostContext, preview: PreviewHost) -> None:
        self.__dict__.update(parent.__dict__)
        self._preview = preview

    def emit(self, view_id: str, port: str, value: Any) -> None:
        self._preview.renderer_emitted(port, value)


class PreviewBridge(QObject):
    """Handed to ``PreviewView.qml`` as ``bridge``: the card's previous / next."""

    def __init__(self, host: PreviewHost) -> None:
        super().__init__()
        self._host = host

    @Slot(int)
    def step(self, delta: int) -> None:
        self._host.step_item(int(delta))


class PreviewHost(Stepping, QuickViewHost):
    """One live preview view; see the module docstring."""

    def __init__(
        self,
        ctx: HostContext,
        view_id: str,
        manifest: ViewManifest,
        *,
        renderers: dict[str, ViewHostFactory] | None = None,
        headless: bool = False,
    ) -> None:
        super().__init__(ctx, view_id, manifest)
        self.collection: Collection | None = None
        self.items: dict[str, Item] = {}
        self.keys: list[str] = []
        self.key: str | None = None
        self.kind: str | None = None  # the renderer's view type; None: none (or a card)
        self.part: str = ""  # the shown part's path
        self.renderer: QuickViewHost | None = None
        self._fed: list[Item] | None = None  # the items the renderer last got on data
        self.card: dict[str, str] | None = None
        self._wanted_key: str | None = None  # the last selection received
        self._renderers = renderers if renderers is not None else builtin_renderers()
        self._renderer_ctx = RendererContext(ctx, self)
        self._slot: QQuickItem | None = None
        self.bridge = PreviewBridge(self)
        if ctx.qml_engine is not None and not headless:
            self.item = ctx.create_item(
                PREVIEW_QML, properties={"viewId": view_id, "bridge": self.bridge}
            )
            self._slot = self.item.findChild(QQuickItem, f"preview:{view_id}:slot")
            if self._slot is not None:
                self._slot.widthChanged.connect(self._fit)
                self._slot.heightChanged.connect(self._fit)
        self.set_health("paused", "no data")

    # -- ports -------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        if port == DATA:
            self.collection = value if isinstance(value, Collection) else None
            items = self.collection.items if self.collection else []
            self.items = {it.key: it for it in items}
            self.keys = list(self.items)
            key = self._wanted_key if self._wanted_key in self.items else self.key
            if key not in self.items:
                key = self.keys[0] if self.keys else None
            self._show(key, fresh=True)
            self.sync_step()
        elif port == SELECTION:
            key = value.picked if isinstance(value, Selection) else None
            self._wanted_key = key
            if key is not None and key in self.items and key != self.key:
                self._show(key)
                self.sync_step()

    # -- kinds -------------------------------------------------------------------------
    def kind_of(self, item: Item) -> tuple[str | None, str]:
        """The renderer for ``item`` and the part it shows; (None, first part) when no
        renderer shows any of its parts."""
        best: tuple[int, str, str] | None = None
        for path in item.parts.values():
            for k in self.ctx.engine.plugins.kinds_for_path(path):
                r = RENDERER_BY_KIND.get(k)
                if r is not None and r in self._renderers:
                    rank = RENDERERS.index(r)
                    if best is None or rank < best[0]:
                        best = (rank, r, path)
                    break
        if best is None:
            return None, next(iter(item.parts.values()), "")
        return best[1], best[2]

    # -- showing -----------------------------------------------------------------------
    def _show(self, key: str | None, *, fresh: bool = False) -> None:
        """Show ``key`` in the renderer of its kind (a new one when the kind changed);
        ``fresh``: the collection changed, so the renderer gets it again."""
        self.key = key
        if key is None:
            self._drop_renderer()
            self._set_card(None)
            self.kind, self.part = None, ""
            self.set_health("paused", "no items" if self.collection is not None else "no data")
            return
        item = self.items[key]
        kind, part = self.kind_of(item)
        self.part = part
        if kind is None:
            self._drop_renderer()
            self.kind = None
            self._set_card(self._card_for(item, part))
            self.set_health("ok", f"{Path(part).name}: no preview")
            return
        self._set_card(None)
        if kind != self.kind or self.renderer is None:
            self._drop_renderer()
            self.kind = kind
            if not self._make_renderer(kind, item, part):
                return
            fresh = True
        self._feed(fresh=fresh)

    def _make_renderer(self, kind: str, item: Item, part: str) -> bool:
        factory = self._renderers[kind]
        plugin: PluginManifest | None = getattr(factory, "plugin", None)
        manifest = next(
            (v for v in (plugin.views if plugin else []) if v.type == kind),
            self.ctx.manifest_for(kind),
        )
        try:
            self.renderer = factory.create(self._renderer_ctx, self.view_id, manifest)
        except Exception as e:  # a missing build (native plugin, vendor libs) is a card
            self.renderer = None
            self._set_card(self._card_for(item, part, hint=f"{kind} renderer: {e}"))
            self.set_health("error", f"{kind} renderer: {type(e).__name__}: {e}")
            return False
        if isinstance(self.renderer, Stepping):
            self.renderer.step_owner = self  # its controls and keys step the preview
        r_item = self.renderer.item
        if r_item is not None and self._slot is not None:
            r_item.setParentItem(self._slot)
            r_item.setX(0)
            r_item.setY(0)
            r_item.setVisible(True)
            self._fit()
        return True

    def _feed(self, *, fresh: bool) -> None:
        """The renderer gets its items (``fresh``; only when they changed, so it keeps
        its page, zoom and loaded file when other kinds come and go) and the shown key."""
        r, key, col = self.renderer, self.key, self.collection
        if r is None or key is None or col is None:
            return
        one = self.kind in ONE_ITEM
        if fresh or one:
            items = (
                [self.items[key]]
                if one
                else [it for it in col.items if self.kind_of(it)[0] == self.kind]
            )
            if items != self._fed:
                self._fed = items
                r.receive(DATA, col.model_copy(update={"items": items}))
        if not one:
            r.receive(SELECTION, Selection(keys=[key], last=key))
        self._kind_icon()

    def _kind_icon(self) -> None:
        """The renderer's footer names the shown file's kind by its icon (#126)."""
        item = self.renderer.item if self.renderer is not None else None
        if item is not None and self.part:
            item.setProperty("kindIcon", icon_for(Path(self.part).name, is_dir=False))

    def _drop_renderer(self) -> None:
        self._fed = None
        r, self.renderer = self.renderer, None
        if r is not None:
            r.dispose()

    @Slot()
    def _fit(self) -> None:
        item = self.renderer.item if self.renderer is not None else None
        if item is not None and self._slot is not None:
            item.setWidth(self._slot.width())
            item.setHeight(self._slot.height())

    # -- the card ----------------------------------------------------------------------
    def _card_for(self, item: Item, part: str, *, hint: str = OPEN_HINT) -> dict[str, str]:
        size = -1
        stat = (item.meta.get("stat") or {}).get(
            next((r for r, p in item.parts.items() if p == part), ""), None
        )
        if isinstance(stat, list | tuple) and stat:
            size = int(stat[0])
        else:
            with contextlib.suppress(OSError):
                size = os.path.getsize(part)
        return {
            "name": Path(part).name or item.key,
            "icon": icon_for(Path(part).name, is_dir=False) if part else "",
            "size": format_size(size) if size >= 0 else "",
            "hint": hint,
        }

    def _set_card(self, card: dict[str, str] | None) -> None:
        self.card = card
        if self.item is not None:
            self.item.setProperty("card", card)

    # -- the renderer's emits and stepping --------------------------------------------
    def renderer_emitted(self, port: str, value: Any) -> None:
        """A renderer moved to another item of its own (an image step, a wikilink): the
        preview shows it and it goes out; everything else stays inside."""
        if port != SELECTION or not isinstance(value, Selection):
            return
        key = value.picked
        if key is None or key == self.key or key not in self.items:
            return
        self.key = self._wanted_key = key
        self.part = self.kind_of(self.items[key])[1]
        self.emit(SELECTION, Selection(keys=[key], last=key))
        self._kind_icon()
        self.sync_step()

    # -- stepping (hosts.stepping): the whole collection, whatever the kind ------------
    def step_keys(self) -> list[str]:
        return self.keys

    def step_key(self) -> str | None:
        return self.key

    def show_step(self, key: str) -> bool:
        self._wanted_key = key
        self._show(key)
        return True

    def on_step_state(self, state: dict[str, int]) -> None:
        """The renderer's controls show the preview's place; the card's are its own."""
        super().on_step_state(state)
        if self.renderer is not None and isinstance(self.renderer, Stepping):
            self.renderer.on_step_state(state)

    @property
    def step_type(self) -> str | None:
        """The view type whose step keys apply now: the renderer's."""
        return self.kind

    # -- contract ----------------------------------------------------------------------
    def on_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        if self.renderer is not None:
            self.renderer.on_patch(patch, meta)

    def pause(self) -> None:
        super().pause()
        if self.renderer is not None:
            self.renderer.pause()

    def resume(self) -> None:
        super().resume()
        if self.renderer is not None:
            self.renderer.resume()

    def snapshot(self) -> dict[str, Any]:
        shown: dict[str, Any] = {}
        if self.renderer is not None:
            try:
                shown = self.renderer.snapshot()
            except (SpectoError, RuntimeError) as e:
                shown = {"error": f"{type(e).__name__}: {e}"}
        return {
            **super().snapshot(),
            "count": len(self.keys),
            "selected": {
                "index": self.keys.index(self.key) if self.key in self.items else -1,
                "key": self.key,
            },
            "renderer": self.kind,
            "file": self.part,
            "card": self.card,
            "shown": shown,
        }

    def snapshot_image(self) -> Any:
        return self.renderer.snapshot_image() if self.renderer is not None else None

    def dispose(self) -> None:
        self._drop_renderer()
        super().dispose()


class PreviewFactory:
    type = "preview"
    plugin = PREVIEW_PLUGIN

    def create(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> PreviewHost:
        return PreviewHost(ctx, view_id, manifest)


def factory() -> PreviewFactory:
    return PreviewFactory()


def register(registry: Any) -> None:
    """Entry point / ``--host-module`` hook: ``registry.add_factory(PreviewFactory())``."""
    registry.add_factory(factory())


__all__ = [
    "DATA",
    "ONE_ITEM",
    "PREVIEW_MANIFEST",
    "PREVIEW_PLUGIN",
    "PREVIEW_QML",
    "RENDERERS",
    "RENDERER_BY_KIND",
    "SELECTION",
    "PreviewBridge",
    "PreviewFactory",
    "PreviewHost",
    "RendererContext",
    "builtin_renderers",
    "factory",
    "register",
]
