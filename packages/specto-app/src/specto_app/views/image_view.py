"""The ``image`` view host (spec #35 story 40; ports per ADR 0014): the full-size picture
of the selected item of its collection.

One :class:`ImageHost` per live image view. It owns ``Specto/Image/ImageView.qml``, a
QML ``Image`` (decoded by Qt's image loader thread, never on the GUI thread) with wheel
zoom around the cursor, drag pan, fit (F, 0) / 1:1 (1) and next / previous. Ports:

- ``data`` in: the collection its intaker resolved (usually a copy of a gallery's data
  link, made by the open-image gesture); the first item shows until a selection says
  otherwise;
- ``selection`` in: the item to show (the last selected key); shown, never re-emitted;
- ``selection`` out: the item the user steps to (``hosts.stepping``: the arrows and
  PageUp / PageDown, the footer's previous / next), so a linked gallery follows.

Zoom, centre and fit are the view's own (reported by ``snapshot()`` with the key, index
and image size); health comes from the image status (``loading`` / ``ok`` / ``error``).

Channels (#171, ``views.channels``): the footer's R G B A toggles (L A for grey with
alpha; none for plain grey), offered from the shown file's header, probed once per file.
A click or the key solos one as greyscale (again: all), Ctrl+click, Alt+click or the
toggle's menu toggles it in the mix (#182); A alone is the alpha as greyscale, A with
colour composites over a checkerboard. The choice is ``params.channels``, written as a
user submit (one undo step) and set by the agent like any param; a GPU shader draws it
over the decoded texture. A preview showing an image keeps it in the preview's own params.
The manifest is registered from this module through ``IMAGE_PLUGIN`` on the factory
(entry point ``image = specto_app.views.image_view:register``).
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QUrl, Slot

from specto_app.drag import DragPayload, payload_for
from specto_app.hosts.base import HostContext, QuickViewHost
from specto_app.hosts.stepping import Stepping
from specto_app.menu import MenuItemRef, MenuTarget
from specto_app.views.channels import CHANNELS_SCHEMA, UNKNOWN, ChannelInfo, ChannelView, probe
from specto_core.errors import SpectoError
from specto_core.kinds import DataRef, Item, PluginManifest, ViewManifest
from specto_core.links import Collection, Selection

DATA, SELECTION = "data", "selection"
IMAGE_QML = Path(__file__).resolve().parent.parent / "qml" / "Specto" / "Image" / "ImageView.qml"


IMAGE_MANIFEST = ViewManifest(
    type="image",
    label="Image",
    host="quick",
    doc="Full-size image of the selected item of its collection, with channel toggles "
    "(params.channels); shows the selection it receives and emits the item it steps to.",
    guide="Wheel zoom around the cursor, drag pan, fit / 1:1, next / previous; the "
    "footer's R G B A toggles (L A for grey with alpha) write params.channels.",
    accepts=["image2d"],
    state_schema={
        "type": "object",
        "properties": {"channels": CHANNELS_SCHEMA},
        "additionalProperties": True,
    },
    example={
        "command": "canvas.submit",
        "args": {"document": {"views": {"tamu": {"type": "image", "label": "front image"}}}},
    },
)
IMAGE_PLUGIN = PluginManifest(name="app-image", version="0.2.0", views=[IMAGE_MANIFEST])


class ImageBridge(QObject):
    """Handed to ``ImageView.qml`` as ``bridge``."""

    def __init__(self, host: ImageHost) -> None:
        super().__init__()
        self._host = host

    @Slot(int)
    def step(self, delta: int) -> None:
        self._host.step_item(int(delta))

    @Slot(str, float, float)
    def imageStatus(self, status: str, width: float, height: float) -> None:
        self._host.on_image_status(str(status), int(width), int(height))

    @Slot(float, bool, float, float)
    def viewChanged(self, zoom: float, fit: bool, cx: float, cy: float) -> None:
        self._host.on_view_changed(float(zoom), bool(fit), float(cx), float(cy))

    @Slot(str, bool)
    def channelClicked(self, channel: str, mix: bool) -> None:
        """A toggle's click or its key (solo), Ctrl/Alt+click or the menu (``mix``)."""
        self._host.channel_gesture(str(channel), mix=bool(mix))

    @Slot()
    def channelReset(self) -> None:
        self._host.channel_gesture("")

    @Slot()
    def dragImage(self) -> None:
        """Ctrl+drag: the shown image out (#185)."""
        self._host.start_drag({"kind": "items"})

    @Slot(float, float, float, float)
    def dragCrop(self, x: float, y: float, w: float, h: float) -> None:
        """A drag from inside the Shift-marked crop (image pixels): the crop out (#185)."""
        self._host.start_drag({"kind": "crop", "rect": [x, y, w, h]})


class ImageHost(Stepping, QuickViewHost):
    """One live image view; see the module docstring."""

    #: released, the item leaves the scene and lets go of its decoded picture (SPEC-3)
    release_when_paused = True

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
        self.items: list[Item] = []
        self.keys: list[str] = []
        self._index_of: dict[str, int] = {}
        self._refs: dict[int, DataRef | None] = {}
        self._infos: dict[int, ChannelInfo] = {}  # probed once per file of the collection
        self.channels = ChannelView()
        self._wanted_key: str | None = None  # the last selection received
        self._url = ""  # the source the item was last given
        self.index = -1
        self.zoom = 1.0
        self.fit = True
        self.center: tuple[float, float] = (0.5, 0.5)
        self.image_size: tuple[int, int] = (0, 0)
        self.image_status = "empty"
        self.bridge = ImageBridge(self)
        if ctx.qml_engine is not None and not headless:
            self.item = ctx.create_item(
                IMAGE_QML, properties={"viewId": view_id, "bridge": self.bridge}
            )
        self._apply_params()
        self.set_health("paused", "no data")

    # -- ports -------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        if port == DATA:
            key = self.key  # the item shown now stays shown when it is still there (#116)
            self.collection = value if isinstance(value, Collection) else None
            self.items = list(self.collection.items) if self.collection else []
            self.keys = [it.key for it in self.items]
            self._index_of = {k: i for i, k in enumerate(self.keys)}
            self._refs = {}
            self._infos = {}
            want = self._wanted_key if self._wanted_key in self._index_of else key
            i = self._index_of.get(want, -1) if want is not None else -1
            if i < 0 and self.items:
                i = 0
            self._show(i)
            if not self.items:
                self.set_health("paused", "no items" if self.collection else "no data")
        elif port == SELECTION:
            key = None
            if isinstance(value, Selection):
                key = value.picked
            self._wanted_key = key
            if key is not None:
                self.show_key(key)

    # -- items -------------------------------------------------------------------------
    def index_of(self, key: str) -> int:
        return self._index_of.get(key, -1)

    def ref_of(self, index: int) -> DataRef | None:
        if index in self._refs:
            return self._refs[index]
        from specto_readers import default_readers

        ref = default_readers().locate(self.items[index], accepts=self.manifest.accepts or None)
        self._refs[index] = ref
        return ref

    def url_of(self, index: int) -> str:
        if index < 0 or index >= len(self.items):
            return ""
        ref = self.ref_of(index)
        if ref is None:
            return ""
        from specto_app.members import ensure_provider, image_url_for

        ensure_provider(self.ctx.qml_engine)
        return image_url_for(ref.uri)

    def info_of(self, index: int) -> ChannelInfo:
        """The channels the file at ``index`` offers, probed once from its header."""
        if index < 0 or index >= len(self.items):
            return UNKNOWN
        if index not in self._infos:
            ref = self.ref_of(index)
            uri = ref.uri if ref is not None else ""
            self._infos[index] = probe(uri) if uri and "://" not in uri else UNKNOWN
        return self._infos[index]

    def caption_of(self, index: int) -> str:
        if index < 0 or index >= len(self.items):
            return f"{len(self.items)} items" if self.items else ""
        # the footer's name (#126): the step controls show the position, the key is the
        # file's own name's start
        it = self.items[index]
        for path in (it.parts or {}).values():
            return Path(path).name
        return it.key

    # -- drag out (#185) ------------------------------------------------------------------
    def shown_target(self) -> MenuTarget | None:
        """The shown image as a target (its key and file), None when nothing shows."""
        ref = self.ref_of(self.index) if self.index >= 0 else None
        if ref is None or self.key is None:
            return None
        file, sep, inner = ref.uri.partition("#")
        item = MenuItemRef(key=self.key, path=os.path.normpath(file) + sep + inner, kind=ref.kind)
        return self.menu_target("items", [item], {"index": self.index})

    def drag_payload(self, hit: dict[str, Any] | None) -> DragPayload | None:
        """The shown image as an ``image`` item; ``{"kind": "crop", "rect": [x, y, w, h]}``
        (image pixels) drags that part of it as a PNG (``meta.crop``, ``meta.size``: the
        whole image's; ``meta.rendered``: the picture, not the file, is the thing dragged,
        so its file URL is left out)."""
        target = self.shown_target()
        if target is None or not target.items:
            return None
        labels = {target.items[0].key: self.caption_of(self.index)}
        if (hit or {}).get("kind") != "crop":
            return payload_for(target, kind="image", labels=labels)
        rect = [round(float(v)) for v in (hit or {}).get("rect", [])[:4]]
        if len(rect) != 4 or rect[2] < 1 or rect[3] < 1:
            return None
        png, size = crop_png(target.items[0].path, rect)
        if png is None:
            raise SpectoError(f"cannot crop {target.items[0].path}")
        return payload_for(
            target,
            kind="image",
            labels=labels,
            png=png,
            meta={"crop": rect, "size": size, "rendered": True},
        )

    # -- pause / resume (contracts sections 11, 12) ---------------------------------------
    def on_release(self) -> None:
        """Released: the item lets go of the decoded image too, and with it the texture
        Qt Quick keeps for it as long as the image is held, in the scene or not (SPEC-3)."""
        if self.item is not None:
            self.item.setProperty("released", True)

    def on_restore(self) -> None:
        """Back: the same file decodes again; zoom and centre come back with it."""
        if self.item is not None:
            self.item.setProperty("released", False)

    # -- showing -----------------------------------------------------------------------
    def _show(self, index: int) -> None:
        self.index = index if 0 <= index < len(self.items) else -1
        url = self.url_of(self.index)
        same = url == self._url
        self._url = url
        self.channels.info = self.info_of(self.index)
        if self.item is not None:
            self.item.setProperty("caption", self.caption_of(self.index))
            self.item.setProperty("source", QUrl(url))
        self._push_channels()
        self.sync_step()
        if self.index >= 0:
            if url and same:
                # the Image ignores a source that does not change and reports no status:
                # the health is the one it has (#116)
                self.on_image_status(self.image_status, *self.image_size)
            elif url:
                self.set_health("loading", self.keys[self.index])
            else:
                self.set_health("error", f"{self.keys[self.index]}: no image part")

    @property
    def key(self) -> str | None:
        return self.keys[self.index] if 0 <= self.index < len(self.keys) else None

    def show_key(self, key: str) -> int:
        """Show ``key`` without emitting (a selection from outside); -1 when unknown."""
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

    # -- channels (#171) ------------------------------------------------------------------
    def channel_gesture(self, channel: str, *, mix: bool = False) -> str:
        """The user soloed (``mix``: toggled) ``channel``, or reset (``""``): shown now and
        written to ``params.channels`` as one user submit. Returns the param."""
        before = self.channels.param
        if not channel:
            self.channels.set_param("")
        elif mix:
            self.channels.mix(channel)
        else:
            self.channels.click(channel)
        param = self.channels.param
        self._push_channels()
        if param != before:
            doc = self.ctx.live.document()
            view = doc["views"].get(self.view_id)
            if view is not None:
                params = view.setdefault("params", {})
                if param:
                    params["channels"] = param
                else:
                    params.pop("channels", None)
                name = view.get("label") or self.view_id
                self.ctx.submit(doc, f"{name}: channels {param.upper() or 'all'}")
        return param

    def _push_channels(self) -> None:
        if self.item is not None:
            self.item.setProperty(
                "channels", {**self.channels.snapshot(), **self.channels.uniforms()}
            )

    def on_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        prefix = f"/views/{self.view_id}"
        if any(
            op.get("path", "").startswith(f"{prefix}/params") or op.get("path") == prefix
            for op in patch
        ):
            self._apply_params()

    def _apply_params(self) -> None:
        """``params.channels`` from the document (the agent's, or a toggle's undone)."""
        try:
            param = self.params.get("channels", "")
        except SpectoError:
            return
        if ChannelView.letters(param) != self.channels.param:
            self.channels.set_param(param)
            self._push_channels()

    # -- the item's own navigation -------------------------------------------------------
    def on_view_changed(self, zoom: float, fit: bool, cx: float, cy: float) -> None:
        """The item zoomed, panned or fitted: remember it for ``snap``."""
        self.zoom, self.fit, self.center = zoom, fit, (cx, cy)

    def on_image_status(self, status: str, width: int, height: int) -> None:
        self.image_status = status
        self.image_size = (width, height)
        key = self.key or ""
        if status == "ready":
            self.set_health("ok", f"{key} {width}x{height}")
        elif status == "error":
            self.set_health("error", f"cannot load {key}")
        elif status == "loading":
            self.set_health("loading", key)

    # -- contract ----------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        zoom, fit = self.zoom, self.fit
        if self.item is not None:
            with contextlib.suppress(Exception):
                zoom = float(self.item.property("zoom"))
                fit = bool(self.item.property("fit"))
        return {
            **super().snapshot(),
            "count": len(self.items),
            "selected": {"index": self.index, "key": self.key},
            "zoom": zoom,
            "fit": fit,
            "channels": self.channels.snapshot(),
            "center": list(self.center),
            "image": {
                "width": self.image_size[0],
                "height": self.image_size[1],
                "status": self.image_status,
                "url": self.url_of(self.index),
            },
        }


def crop_png(path: str, rect: list[int]) -> tuple[bytes | None, list[int]]:
    """``rect`` (x, y, w, h in the shown image's pixels: EXIF orientation applied) of the
    image file as PNG bytes, and the whole image's size; None when it cannot be read.
    An image stored upright decodes only that part."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect
    from PySide6.QtGui import QImageIOHandler, QImageReader

    reader = QImageReader(path)
    reader.setAutoTransform(True)
    upright = reader.transformation() == QImageIOHandler.Transformation.TransformationNone
    size = reader.size()
    box = QRect(*rect)
    if upright and size.isValid():
        reader.setClipRect(box.intersected(QRect(0, 0, size.width(), size.height())))
        img = reader.read()
        whole = [size.width(), size.height()]
    else:
        full = reader.read()
        img = full.copy(box) if not full.isNull() else full
        whole = [full.width(), full.height()]
    if img.isNull():
        return None, whole
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data.data()), whole


class ImageFactory:
    type = "image"
    plugin = IMAGE_PLUGIN

    def create(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> ImageHost:
        return ImageHost(ctx, view_id, manifest)


def factory() -> ImageFactory:
    return ImageFactory()


def register(registry: Any) -> None:
    """Entry point / ``--host-module`` hook: ``registry.add_factory(ImageFactory())``."""
    registry.add_factory(factory())


__all__ = [
    "DATA",
    "IMAGE_MANIFEST",
    "IMAGE_PLUGIN",
    "IMAGE_QML",
    "SELECTION",
    "ImageBridge",
    "ImageFactory",
    "ImageHost",
    "factory",
    "register",
]
