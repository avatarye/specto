"""The context-menu registry (#154): entries declared as data, matched against a target.

A **target** (:class:`MenuTarget`) is what the pointer (or the keyboard's focus) is on:
one view, and in it some items, the view itself, or its empty background. The view's
host builds it (``QuickViewHost.context_target``); nothing here knows Qt or a view.

An **entry** (:class:`MenuEntry`) is one row of the menu: an id, a label, an optional
icon (a shell icon name) and shortcut (shown, not bound: the key that does the same in
the view), a scope (view roles, view types, target kinds), ``visible`` / ``enabled``
predicates on the target, a group with an order inside it, and a handler. Groups follow
the registry's ``groups`` order (unknown groups after, by name) with a separator between
them. A handler gets a :class:`MenuCall` (the target, the view's host, the clipboard) and
returns a short result (the text it copied, the view it opened) or None.

Entries built from configuration (the launchers, #186) name their label per target
(``text``) and may hold a **submenu**: ``submenu(target)`` returns its entries, shown in
the order given with a separator where the group changes; a child runs as
``<parent id>/<child id>``. One level deep.

The app's menu is :func:`default_registry`; a view module adds its entries at import
(``default_registry().add(MenuEntry(...))``), so a new entry needs no new UI code.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

#: what a target is on: some items of the view, the view as a whole, its empty background
TargetKind = Literal["items", "view", "background"]

#: the group order of the app's menu: opening things, copying things, the view itself
GROUPS = ("open", "copy", "view")


@dataclass(frozen=True)
class MenuItemRef:
    """One item under the cursor: the view's key for it (a tree path, a gallery key) and
    the file behind it (absolute, native separators; "" when it is no file)."""

    key: str
    path: str = ""
    is_dir: bool = False
    kind: str = ""  # the data kind when the view knows it (image, video, ...)


@dataclass(frozen=True)
class MenuTarget:
    """What a context menu is about. ``root`` is the project folder (relative paths are
    relative to it; "" when there is none); ``data`` is whatever else the view or its
    page said about the spot (a web page's own target object)."""

    view_id: str
    view_type: str
    role: str
    kind: TargetKind = "view"
    items: tuple[MenuItemRef, ...] = ()
    root: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class MenuCall:
    """What a handler runs with: the target, the view's host (None headless) and the
    clipboard (a callable taking the text)."""

    target: MenuTarget
    host: Any = None
    clipboard: Callable[[str], None] | None = None

    def copy(self, text: str) -> str:
        if self.clipboard is not None:
            self.clipboard(text)
        return text


Predicate = Callable[[MenuTarget], bool]


@dataclass(frozen=True)
class MenuEntry:
    """One menu row, as data (see the module docstring). Empty ``roles`` / ``view_types``
    match any view."""

    id: str
    label: str
    handler: Callable[[MenuCall], Any] | None = None
    icon: str = ""
    shortcut: str = ""
    group: str = "view"
    order: int = 0
    roles: tuple[str, ...] = ()
    view_types: tuple[str, ...] = ()
    targets: tuple[str, ...] = ("items",)
    visible: Predicate | None = None
    enabled: Predicate | None = None
    #: the label for this target (``label`` when None or when it raises)
    text: Callable[[MenuTarget], str] | None = None
    #: the child entries for this target: the row opens them (an empty list hides it)
    submenu: Callable[[MenuTarget], Sequence[MenuEntry]] | None = None

    def applies(self, target: MenuTarget) -> bool:
        return (
            target.kind in self.targets
            and (not self.roles or target.role in self.roles)
            and (not self.view_types or target.view_type in self.view_types)
            and _holds(self.visible, target)
        )

    def is_enabled(self, target: MenuTarget) -> bool:
        return _holds(self.enabled, target)

    def label_for(self, target: MenuTarget) -> str:
        if self.text is None:
            return self.label
        try:
            return str(self.text(target))
        except Exception:
            return self.label

    def children(self, target: MenuTarget) -> list[MenuEntry]:
        """The submenu's entries that apply to ``target`` ([] without a submenu; a
        submenu that raises is empty)."""
        if self.submenu is None:
            return []
        try:
            entries = list(self.submenu(target))
        except Exception:
            return []
        return [e for e in entries if e.applies(target)]


def _holds(pred: Predicate | None, target: MenuTarget) -> bool:
    """A predicate that raises counts as false: one broken entry never breaks the menu."""
    if pred is None:
        return True
    try:
        return bool(pred(target))
    except Exception:
        return False


class ContextMenuRegistry:
    """The entries, in the order they were added; see the module docstring."""

    def __init__(self, entries: Iterable[MenuEntry] = (), *, groups: tuple[str, ...] = GROUPS):
        self.groups = groups
        self._entries: dict[str, MenuEntry] = {}
        for e in entries:
            self.add(e)

    def add(self, entry: MenuEntry, *, replace: bool = False) -> MenuEntry:
        if "/" in entry.id:
            raise ValueError(f"context-menu entry id {entry.id!r} has a '/' (a submenu path)")
        if entry.id in self._entries and not replace:
            raise ValueError(f"context-menu entry {entry.id!r} is already registered")
        self._entries[entry.id] = entry
        return entry

    def remove(self, entry_id: str) -> None:
        self._entries.pop(entry_id, None)

    def get(self, entry_id: str) -> MenuEntry | None:
        return self._entries.get(entry_id)

    def entries(self) -> list[MenuEntry]:
        return list(self._entries.values())

    def entries_for(self, target: MenuTarget) -> list[MenuEntry]:
        """The entries shown for ``target``, in menu order (a submenu with no entry for
        it is not shown)."""
        found = [
            e
            for e in self._entries.values()
            if e.applies(target) and (e.submenu is None or e.children(target))
        ]
        seq = {e.id: i for i, e in enumerate(self._entries.values())}
        return sorted(found, key=lambda e: (self._group_rank(e.group), e.order, seq[e.id]))

    def _group_rank(self, group: str) -> tuple[int, str]:
        return (self.groups.index(group), "") if group in self.groups else (len(self.groups), group)

    def rows(self, target: MenuTarget) -> list[dict[str, Any]]:
        """The menu as the shell's ``PopMenu`` draws it: ``{name, text, icon, detail,
        enabled}`` per entry, ``{separator: true}`` between groups; a submenu's row also
        has ``children``, its own rows (named ``<parent id>/<child id>``)."""
        return _rows(self.entries_for(target), target)

    def run(self, entry_id: str, call: MenuCall) -> Any:
        """Run the entry (``<parent>/<child>`` for a submenu's) on ``call.target``; None
        when it is unknown, hidden or disabled there."""
        parent_id, _, child_id = entry_id.partition("/")
        e = self._entries.get(parent_id)
        if e is None or not e.applies(call.target) or not e.is_enabled(call.target):
            return None
        if child_id:
            e = next((c for c in e.children(call.target) if c.id == child_id), None)
            if e is None or not e.is_enabled(call.target):
                return None
        return e.handler(call) if e.handler is not None else None


def _rows(entries: list[MenuEntry], target: MenuTarget, prefix: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    last: str | None = None
    for e in entries:
        if last is not None and e.group != last:
            rows.append({"separator": True})
        last = e.group
        row: dict[str, Any] = {
            "name": prefix + e.id,
            "text": e.label_for(target),
            "icon": e.icon,
            "detail": e.shortcut,
            "enabled": e.is_enabled(target),
        }
        if e.submenu is not None and not prefix:
            row["children"] = _rows(e.children(target), target, prefix=f"{e.id}/")
        rows.append(row)
    return rows


__all__ = [
    "GROUPS",
    "ContextMenuRegistry",
    "MenuCall",
    "MenuEntry",
    "MenuItemRef",
    "MenuTarget",
    "Predicate",
    "TargetKind",
]
