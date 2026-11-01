"""``json`` view: Monaco read-only JSON of the selected item of its collection (spec #35
story 44; ports per ADR 0014).

Ports: ``data`` (the collection its intaker resolved) and ``selection`` (which item to
show; the last selected key) in; ``content`` (the shown file, parsed, as a
``ContentValue``) and ``selection`` (a pick made inside the view: previous / next,
``hosts.stepping``, the page's ``step`` event) out. The host turns the shown item into
``{key, url}`` where ``url`` is the item's JSON part on the ``specto://items`` host,
served under ``read:link:<data link>``; the page fetches it. Which part: the first of
the manifest's accepted kinds the item has, by extension. ``snapshot()`` reports the
shown key and a short outline of the document (top-level keys and value types), read
here in Python so ``snap`` works without asking the page. ``params.wrap`` is the
page's word wrap, written by the page.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from specto_core.contents import ContentValue, format_for, read_entry
from specto_core.kinds import Item, ViewManifest
from specto_core.links import Collection, Selection

from ..hosts.base import HostContext, SimpleFactory
from ..hosts.stepping import Stepping
from ..menu import MenuItemRef
from ..web.items import display_names, pick_role
from ..web.scheme import item_url
from ..web.webview import WebViewHost

#: data kind -> part roles (extensions), in preference order
ROLES_BY_KIND: dict[str, tuple[str, ...]] = {
    "json": (".json",),
    "geojson": (".geojson", ".json"),
    "markdown": (".md", ".markdown"),
    "text": (".txt", ".csv", ".yaml", ".yml", ".toml", ".log"),
}

OUTLINE_MAX = 24
SNAPSHOT_KEYS_CAP = 256
NO_ITEM = "(no item selected)"
DATA, SELECTION, CONTENT = "data", "selection", "content"


def outline_of(path: str | Path, limit: int = OUTLINE_MAX) -> list[str]:
    """``["frame: int", "pose: object{4}", ...]`` for a JSON file; ``[]`` when it is not JSON."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return outline_of_value(data, limit)


def outline_of_value(data: Any, limit: int = OUTLINE_MAX) -> list[str]:
    """:func:`outline_of` for parsed JSON (a content entry shown inline)."""

    def kind(v: Any) -> str:
        if isinstance(v, dict):
            return f"object{{{len(v)}}}"
        if isinstance(v, list):
            return f"array[{len(v)}]"
        if isinstance(v, bool):
            return "bool"
        if v is None:
            return "null"
        if isinstance(v, int | float):
            return "number"
        return type(v).__name__ if not isinstance(v, str) else "string"

    if isinstance(data, dict):
        out = [f"{k}: {kind(v)}" for k, v in list(data.items())[:limit]]
        if len(data) > limit:
            out.append(f"... {len(data) - limit} more")
        return out
    return [kind(data)]


def selected_key(value: Any) -> str | None:
    if isinstance(value, Selection):
        return value.picked
    if isinstance(value, dict):
        return value.get("last") or (value.get("keys") or [None])[-1]
    return None


class JsonViewHost(Stepping, WebViewHost):
    page = "json/index.html"
    #: the output port the shown file goes out on as a ``ContentValue``; None for a
    #: host whose type declares no content port (the html view)
    content_port: str | None = CONTENT

    def __init__(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> None:
        super().__init__(ctx, view_id, manifest)
        self.collection: Collection | None = None
        self.items: dict[str, Item] = {}
        self.names: dict[str, str] = {}
        self.current_key: str | None = None
        self.current_url: str | None = None
        self.current_path: str | None = None
        self.current_data: Any = None  # a content value's entry shown inline
        self._wanted_key: str | None = None  # a selection that arrived before the data
        self.set_health("paused", "no data")

    def wanted_roles(self) -> list[str]:
        out: list[str] = []
        for kind in self.manifest.accepts:
            out.extend(r for r in ROLES_BY_KIND.get(kind, ()) if r not in out)
        return out

    @property
    def data_link(self) -> str:
        return self.collection.link if self.collection is not None else ""

    # -- ports -------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        self.refresh_grants()
        if port == DATA:
            self.collection = value if isinstance(value, Collection) else None
            self.items = {it.key: it for it in (self.collection.items if self.collection else [])}
            self.names = display_names(
                (k, next(iter(it.parts.values()), "")) for k, it in self.items.items()
            )
            self.on_data_changed()
            key = self._wanted_key if self._wanted_key in self.items else self.current_key
            if key not in self.items:
                key = next(iter(self.items), None)
            self.show_key(key)
            if not self.items:
                self.set_health("paused", "no items" if self.collection is not None else "no data")
        elif port == SELECTION:
            key = selected_key(value)
            self._wanted_key = key
            if key is not None:
                self.show_key(key)
        elif port == CONTENT and self.content_port:
            self.show_content(value if isinstance(value, ContentValue) else None)

    def on_data_changed(self) -> None:
        """The collection changed (the markdown host sends its note list here)."""

    # -- the shown item -----------------------------------------------------------------
    def item_payload(self, key: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"key": key, "url": None, "roles": []}
        it = self.items.get(key)
        if it is None:
            payload["reason"] = f"item {key} is not in the collection"
            return payload
        parts = dict(it.parts)
        payload["roles"] = sorted(parts)
        payload["name"] = self.names.get(key, key)
        role = pick_role(parts, self.wanted_roles())
        if role is None:
            payload["reason"] = f"item {key} has no readable part (has {', '.join(sorted(parts))})"
            return payload
        payload["url"] = item_url(self.data_link, key, role)
        payload["role"] = role
        payload["file"] = Path(parts[role]).name
        payload["path"] = parts[role]
        return payload

    def show_key(self, key: str | None) -> dict[str, Any]:
        """Show ``key`` (None: nothing): the page gets the item, ``content`` gets the
        file. A key that is not in the collection is reported; the shown item stays."""
        if key is None:
            payload: dict[str, Any] = {"key": None, "url": None}
            self.current_key = self.current_url = self.current_path = None
            if self.content_port:
                self.emit(self.content_port, ContentValue(root=self._root()))
        else:
            payload = self.item_payload(key)
            if payload["url"] is not None:
                self.current_key = key
                self.current_url = payload["url"]
                self.current_path = payload["path"]
                if self.content_port:
                    self.emit(self.content_port, self.content_of(key, payload["path"]))
        self.notify("item", {k: v for k, v in payload.items() if k != "path"})
        self.sync_step()
        return payload

    def show_content(self, value: ContentValue | None) -> None:
        """A content value linked in (#140): its first entry, inline (it has no file on
        the items host); none shows nothing. Not republished on ``content``."""
        entry = value.entries[0] if value is not None and value.entries else None
        self.current_url = self.current_path = None
        if entry is None:
            self.current_key = self.current_data = None
            self.notify("item", {"key": None, "url": None})
            self.set_health("ok", NO_ITEM)
            return
        self.current_key, self.current_data = entry.key, entry.data
        data = entry.data
        self.notify(
            "item",
            {
                "key": entry.key,
                "url": None,
                "text": data if isinstance(data, str) else json.dumps(data, indent=2),
                "role": ".txt" if entry.format == "text" else ".json",
                "roles": [],
                "name": entry.key,
                "file": entry.path,
            },
        )

    # -- stepping (hosts.stepping): the items the view can show, in collection order ----
    def step_keys(self) -> list[str]:
        roles = self.wanted_roles()
        return [k for k, it in self.items.items() if pick_role(dict(it.parts), roles) is not None]

    def step_key(self) -> str | None:
        return self.current_key

    def show_step(self, key: str) -> bool:
        self._wanted_key = key
        return self.show_key(key)["url"] is not None

    def on_step_state(self, state: dict[str, int]) -> None:
        self.notify("step", state)

    def _root(self) -> str:
        if self.collection is not None and self.collection.root:
            return self.collection.root
        return str(self.ctx.project_root or "")

    def content_of(self, key: str, path: str) -> ContentValue:
        root = self._root()
        rel = Path(path).name
        if root:
            with contextlib.suppress(ValueError):  # another drive: the name alone
                rel = os.path.relpath(path, root).replace("\\", "/")
        fmt = format_for(path) or "text"
        return ContentValue(root=root, entries=[read_entry(path, rel, fmt)])

    # -- hooks -------------------------------------------------------------------------
    def on_ready(self) -> None:
        if self.current_key is not None:
            self.show_key(self.current_key)
        else:
            self.notify("item", {"key": None, "url": None})
            self.sync_step()
            self.set_health("ok", NO_ITEM)

    def text_source(self) -> MenuItemRef | None:
        """A dragged selection comes from the shown file (#185)."""
        if self.current_key is None or not self.current_path:
            return None
        return MenuItemRef(key=self.current_key, path=os.path.normpath(self.current_path))

    def text_label(self, ref: MenuItemRef) -> str:
        return self.names.get(ref.key) or super().text_label(ref)

    def on_page_event(self, kind: str, payload: Any) -> None:
        """``step {delta}``: the page's previous / next control or key."""
        if kind == "step" and isinstance(payload, dict):
            with contextlib.suppress(TypeError, ValueError):
                self.step_item(int(payload.get("delta") or 0))

    def snapshot(self) -> dict[str, Any]:
        snap = super().snapshot()
        keys = list(self.items)
        snap.update(
            {
                "item": self.current_key,
                "url": self.current_url,
                "count": len(keys),
                "keys": keys[:SNAPSHOT_KEYS_CAP],
                "outline": outline_of(self.current_path)
                if self.current_path
                else outline_of_value(self.current_data)
                if self.current_data is not None
                else [],
            }
        )
        return snap


def factory() -> SimpleFactory:
    return SimpleFactory("json", JsonViewHost)


def register(registry: Any) -> None:
    """``specto.view_hosts`` entry point."""
    registry.add_factory(factory())


__all__ = [
    "ROLES_BY_KIND",
    "JsonViewHost",
    "factory",
    "outline_of",
    "outline_of_value",
    "register",
    "selected_key",
]
