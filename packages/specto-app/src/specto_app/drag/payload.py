"""What a drag out of a view carries (#185; the contract: ``docs/contracts.md``
section 11): Qt-free.

A :class:`DragPayload` is the dragged items (:class:`DragItem`: the view, its type, the
item's key, its path relative to the project and absolute, its kind, a label, meta),
the dragged text (a selection), an optional rendered picture (PNG bytes, an image
view's crop) and the project root. :meth:`DragPayload.formats` turns it into the mime
data every drop target reads, Specto's own first:

- ``application/x-specto-item+json``: ``{"items": [...], "text": ..., "project": ...}``;
- ``image/png``: the picture, when there is one;
- ``text/uri-list``: ``file:///`` URLs of the dragged files (CRLF separated, RFC 2483);
- ``text/html``: the selection's markup, when a page gave it;
- ``text/plain``: the text, or the files' paths (native separators) one per line.

A view says what is under the pointer the way it does for its context menu
(``context_target``: a :class:`~specto_app.menu.MenuTarget`); :func:`payload_for` makes
the payload from that target, so a row, a selection or a tile drags exactly what its
menu would act on.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from specto_core.kinds import BUILTIN_LOCATORS

from ..menu.registry import MenuItemRef, MenuTarget

SPECTO_MIME = "application/x-specto-item+json"
PNG_MIME = "image/png"
URI_MIME = "text/uri-list"
HTML_MIME = "text/html"
TEXT_MIME = "text/plain"

#: what a dragged item is
KINDS = ("file", "image", "text", "prim", "feature", "frame")

IMAGE_EXTS = frozenset(
    ext for loc in BUILTIN_LOCATORS if loc.name == "image" for ext in loc.extensions
)


@dataclass(frozen=True)
class DragItem:
    """One dragged thing. ``path`` is relative to the project root with ``/`` (absolute
    when the file is outside it); ``abs`` is absolute with ``/``; both "" when the item
    is no file (a text with no source). A container entry keeps its ``#inner`` address."""

    view: str
    type: str
    key: str = ""
    path: str = ""
    abs: str = ""
    kind: str = "file"
    label: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "view": self.view,
            "type": self.type,
            "key": self.key,
            "path": self.path,
            "abs": self.abs,
            "kind": self.kind,
            "label": self.label,
            "meta": dict(self.meta),
        }

    @property
    def address(self) -> str:
        """``abs`` with native separators (a container entry keeps its ``#inner``)."""
        file, sep, inner = self.abs.partition("#")
        return os.path.normpath(file) + sep + inner if file else ""

    @property
    def file(self) -> str:
        """The file on disk behind the item (native separators), "" when there is none
        (no path, or an entry inside a container)."""
        if not self.abs or "#" in self.abs:
            return ""
        return os.path.normpath(self.abs)


@dataclass(frozen=True)
class DragPayload:
    """See the module docstring."""

    items: tuple[DragItem, ...] = ()
    text: str | None = None
    project: str = ""
    png: bytes | None = None
    html: str | None = None
    #: the files go out as ``text/uri-list`` too (False: the picture is what is dragged,
    #: an outside app must not take the whole file instead)
    uri_list: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "items": [i.to_json() for i in self.items],
            "text": self.text,
            "project": self.project,
        }

    def files(self) -> list[str]:
        """The dragged files on disk, in order (native separators)."""
        return [f for f in (i.file for i in self.items) if f]

    def uris(self) -> list[str]:
        return [Path(f).as_uri() for f in self.files()] if self.uri_list else []

    def plain_text(self) -> str:
        """The text, else the items' paths one per line (native separators; a container
        entry keeps its ``#inner`` address), else their labels."""
        if self.text is not None:
            return self.text
        return "\n".join(i.address or i.label or i.key for i in self.items)

    def formats(self) -> dict[str, bytes]:
        """The mime data, in the order drop targets read it (see the module docstring)."""
        out = {SPECTO_MIME: json.dumps(self.to_json(), ensure_ascii=False).encode("utf-8")}
        if self.png:
            out[PNG_MIME] = bytes(self.png)
        uris = self.uris()
        if uris:
            out[URI_MIME] = "".join(u + "\r\n" for u in uris).encode("utf-8")
        if self.html:
            out[HTML_MIME] = self.html.encode("utf-8")
        text = self.plain_text()
        if text:
            out[TEXT_MIME] = text.encode("utf-8")
        return out

    @property
    def label(self) -> str:
        """A short name for the drag picture: the first item's label (and how many more),
        or the text's start."""
        if self.items:
            first = self.items[0].label or self.items[0].key or "item"
            more = len(self.items) - 1
            return f"{first}  +{more}" if more else first
        text = " ".join((self.text or "").split())
        return text if len(text) <= 40 else text[:39] + "…"


def posix(path: str) -> str:
    return path.replace("\\", "/")


def relative_to(path: str, root: str) -> str:
    """``path`` relative to ``root`` with ``/``; the absolute path (``/``) when it is not
    under it (or there is no root)."""
    file, sep, inner = path.partition("#")
    rel = None
    if root and file:
        try:
            rel = os.path.relpath(file, root)
        except ValueError:  # another drive
            rel = None
        if rel is not None and (rel == os.pardir or rel.startswith(os.pardir + os.sep)):
            rel = None
    return posix(rel if rel is not None else file) + sep + inner


def kind_of(ref: MenuItemRef) -> str:
    """``image`` for an image file (by its data kind or extension), else ``file``."""
    if ref.is_dir:
        return "file"
    if ref.kind in ("image", "image2d"):
        return "image"
    ext = os.path.splitext(ref.path.partition("#")[0])[1].lower()
    return "image" if ext in IMAGE_EXTS else "file"


def item_from_ref(
    target: MenuTarget,
    ref: MenuItemRef,
    *,
    kind: str | None = None,
    label: str | None = None,
    meta: dict[str, Any] | None = None,
) -> DragItem:
    m = dict(meta or {})
    if ref.is_dir:
        m.setdefault("dir", True)
    file, _, inner = ref.path.partition("#")
    name = os.path.basename((inner or file).rstrip("\\/"))
    return DragItem(
        view=target.view_id,
        type=target.view_type,
        key=ref.key,
        path=relative_to(ref.path, target.root) if ref.path else "",
        abs=posix(ref.path),
        kind=kind or kind_of(ref),
        label=label if label is not None else (name or ref.key),
        meta=m,
    )


def payload_for(
    target: MenuTarget,
    *,
    kind: str | None = None,
    labels: dict[str, str] | None = None,
    meta: dict[str, Any] | None = None,
    text: str | None = None,
    png: bytes | None = None,
    html: str | None = None,
    uri_list: bool | None = None,
) -> DragPayload:
    """The payload for a view's target: one item per target item (``labels`` by key
    override the file names; ``kind`` and ``meta`` apply to every item). The items' files
    go out as URLs unless the drag is a text or a picture (``uri_list`` decides when
    given): an outside app would take the whole file instead."""
    labels = labels or {}
    items = tuple(
        item_from_ref(target, ref, kind=kind, label=labels.get(ref.key), meta=meta)
        for ref in target.items
    )
    if uri_list is None:
        uri_list = kind != "text" and png is None
    return DragPayload(
        items=items, text=text, project=posix(target.root), png=png, html=html, uri_list=uri_list
    )


__all__ = [
    "HTML_MIME",
    "IMAGE_EXTS",
    "KINDS",
    "PNG_MIME",
    "SPECTO_MIME",
    "TEXT_MIME",
    "URI_MIME",
    "DragItem",
    "DragPayload",
    "item_from_ref",
    "kind_of",
    "payload_for",
    "relative_to",
]
