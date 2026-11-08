"""How ``specto://items/<link>/<key>/<role>`` finds its bytes: an ``ItemResolver``
answers ``parts(link_id, key) -> {role: path}``.

- ``LinkItems``: the real one, over the link runtime: the value a link delivered to a
  view's input port (a ``Collection`` of items, or a ``ContentValue`` of read files),
  found by the link id the runtime stamped on it.
- ``ItemTable``: fed from an ``Item`` list (tests, scripts).
- ``CompositeItems``: first resolver that knows the link wins.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from specto_core.contents import ContentValue
from specto_core.kinds import Item
from specto_core.links import Collection, LinkRuntime


@runtime_checkable
class ItemResolver(Protocol):
    def parts(self, link_id: str, key: str) -> dict[str, str] | None:
        """``{role: absolute path}`` for ``key`` on ``link_id``; ``None`` when unknown."""
        ...

    def keys(self, link_id: str) -> list[str]: ...

    def root(self, link_id: str) -> str | None:
        """The folder a part's ``@root/`` relative paths resolve under (the collection's
        root); None: this resolver serves none."""
        return None

    def guard_root(self, link_id: str) -> str | None:
        """The folder a part's own path is judged against before anything is stat'ed
        (``specto_core.pathguard.output_refusal``): a network part is served only when this
        is a plain network path and the part lies under it (a project on a share, SPEC-91);
        None: no network part is."""
        return None


def _table_of(items: Iterable[Item | dict[str, Any]]) -> dict[str, dict[str, str]]:
    table: dict[str, dict[str, str]] = {}
    for it in items:
        if isinstance(it, Item):
            table[it.key] = dict(it.parts)
        else:
            table[str(it["key"])] = dict(it.get("parts", {}))
    return table


class ItemTable:
    """Items per link, as an intaker produces them (``Item.key`` -> ``Item.parts``);
    ``root`` is the folder they were found under (``guard_root``: a table on a share
    serves the share's parts)."""

    def __init__(self, root: str | None = None) -> None:
        self._cols: dict[str, dict[str, dict[str, str]]] = {}
        self._guard_root = root

    def set_items(self, link_id: str, items: Iterable[Item | dict[str, Any]]) -> None:
        self._cols[link_id] = _table_of(items)

    def parts(self, link_id: str, key: str) -> dict[str, str] | None:
        return self._cols.get(link_id, {}).get(key)

    def keys(self, link_id: str) -> list[str]:
        return list(self._cols.get(link_id, {}))

    def root(self, link_id: str) -> str | None:
        return None

    def guard_root(self, link_id: str) -> str | None:
        return self._guard_root if link_id in self._cols else None

    def cols(self) -> list[str]:
        return list(self._cols)


class LinkItems:
    """The values on the link runtime's input ports, by the link that delivered them.
    Nothing is cached: the runtime holds the latest value per port, and a link that
    goes away takes its items with it."""

    def __init__(self, links: LinkRuntime) -> None:
        self.links = links

    def value_of(self, link_id: str) -> Collection | ContentValue | None:
        for v in self.links.inputs.values():
            if isinstance(v, Collection | ContentValue) and v.link == link_id:
                return v
        return None

    def parts(self, link_id: str, key: str) -> dict[str, str] | None:
        v = self.value_of(link_id)
        if isinstance(v, Collection):
            it = next((i for i in v.items if i.key == key), None)
            if it is None:
                return None
            parts = dict(it.parts)
            # what a resolve step derived from the item (the 3d view's decoded point
            # buffer, kd-tree and GLB under meta.scene.files) is served as extra roles
            scene = it.meta.get("scene") if isinstance(it.meta, dict) else None
            for role, path in ((scene or {}).get("files") or {}).items():
                parts["." + str(role).lstrip(".")] = str(path)
            return parts
        if isinstance(v, ContentValue):
            e = v.entry(key)
            if e is None:
                return None
            return {Path(e.path).suffix.lower(): str(Path(v.root) / e.path)}
        return None

    def root(self, link_id: str) -> str | None:
        """The folder the link's collection was found under (the project folder)."""
        v = self.value_of(link_id)
        return (v.root or None) if isinstance(v, Collection) else None

    def guard_root(self, link_id: str) -> str | None:
        """The root of the value the link delivered (a collection's or a content value's,
        already judged against the project when it was built), else the project's."""
        v = self.value_of(link_id)
        if v is None:
            return None
        return v.root or getattr(self.links, "root", "") or None

    def keys(self, link_id: str) -> list[str]:
        v = self.value_of(link_id)
        if isinstance(v, Collection):
            return [i.key for i in v.items]
        if isinstance(v, ContentValue):
            return [e.key for e in v.entries]
        return []


class CompositeItems:
    """Try resolvers in order; the first that knows the key answers."""

    def __init__(self, resolvers: Iterable[ItemResolver] = ()) -> None:
        self.resolvers: list[ItemResolver] = list(resolvers)

    def add(self, resolver: ItemResolver) -> None:
        if resolver not in self.resolvers:
            self.resolvers.append(resolver)

    def remove(self, resolver: ItemResolver) -> None:
        if resolver in self.resolvers:
            self.resolvers.remove(resolver)

    def parts(self, link_id: str, key: str) -> dict[str, str] | None:
        for r in self.resolvers:
            p = r.parts(link_id, key)
            if p:
                return p
        return None

    def keys(self, link_id: str) -> list[str]:
        for r in self.resolvers:
            k = r.keys(link_id)
            if k:
                return k
        return []

    def root(self, link_id: str) -> str | None:
        """The root of the resolver that answers for the link (the one whose ``parts``
        answer), never another resolver's."""
        for r in self.resolvers:
            if r.keys(link_id):
                return r.root(link_id)
        return None

    def guard_root(self, link_id: str) -> str | None:
        """The guard root of the resolver that answers for the link, never another's."""
        for r in self.resolvers:
            if r.keys(link_id):
                return r.guard_root(link_id)
        return None


def pick_role(parts: dict[str, str], wanted: Iterable[str]) -> str | None:
    """First role in ``wanted`` (``[".json", ".geojson"]``) present in ``parts``."""
    for r in wanted:
        if r in parts:
            return r
    return None


_NAMESPACE_RE = re.compile(r"^\d+:")


def display_name(key: str) -> str:
    """A key without the ``<source index>:`` namespace a multi-source intaker adds."""
    return _NAMESPACE_RE.sub("", key)


def display_names(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    """``{key: name}`` for ``(key, primary part path)`` pairs: the key without its
    namespace, and when two items would share a name, the primary part's folder is
    prepended (``img/front`` next to ``front``)."""
    pairs = list(items)
    names = {k: display_name(k) for k, _ in pairs}
    counts: dict[str, int] = {}
    for n in names.values():
        counts[n] = counts.get(n, 0) + 1
    for k, first in pairs:
        n = names[k]
        if counts[n] > 1:
            folder = Path(first).parent.name if first else ""
            names[k] = f"{folder}/{n}" if folder else n
    return names


__all__ = [
    "CompositeItems",
    "ItemResolver",
    "ItemTable",
    "LinkItems",
    "display_name",
    "display_names",
    "pick_role",
]
