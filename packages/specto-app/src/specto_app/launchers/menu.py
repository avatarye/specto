"""The launchers in the context menu (#186), on items that are files in any view: "Open
with <default launcher>" (or the system default) first, then an "Open with" submenu: the
launchers matching every selected file (several files go to an ``each = false``
launcher at once), System default (disabled for programs and scripts), Show in
Explorer, Edit launchers... (the machine's file in the system editor; the shell's
watcher reloads it on save) and Edit project launchers... (the project's
``.specto/launchers.toml``, #204; created from a commented example on first use). The
rows come from the configuration each time the menu opens, through the registry's
dynamic labels and submenus.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from ..menu import MenuCall, MenuEntry, MenuTarget, default_registry
from .book import SYSTEM_LABEL, LauncherBook, book_for, file_of
from .config import Launcher

OPEN_WITH = "launch.open_with"
DEFAULT = "launch.default"


def book(target: MenuTarget) -> LauncherBook:
    return book_for(target.root or None)


def items(target: MenuTarget) -> list[tuple[str, bool]]:
    """The target's files, ``(path, is_dir)``; [] when an item is no file."""
    out = []
    for i in target.items:
        path = file_of(i.path, target.root or None)
        if not path:
            return []
        out.append((path, i.is_dir or os.path.isdir(path)))
    return out


def paths(target: MenuTarget) -> list[str]:
    return [p for p, _ in items(target)]


def default_launcher(target: MenuTarget) -> Launcher | None:
    return book(target).default_for(items(target)) if items(target) else None


def _default_label(target: MenuTarget) -> str:
    lc = default_launcher(target)
    return f"Open with {lc.label if lc is not None else SYSTEM_LABEL.lower()}"


def _default_visible(target: MenuTarget) -> bool:
    if not items(target):
        return False
    return default_launcher(target) is not None or not book(target).system_refusal(paths(target))


def _open_default(call: MenuCall) -> str:
    t = call.target
    lc = default_launcher(t)
    b = book(t)
    done = b.run(lc, paths(t)) if lc is not None else b.open_default(paths(t))
    return done.note


def _launch(launcher_id: str) -> Callable[[MenuCall], str]:
    def handler(call: MenuCall) -> str:
        t = call.target
        b = book(t)
        lc = b.get(launcher_id)
        if lc is None:
            return ""
        return b.run(lc, paths(t)).note

    return handler


def submenu(target: MenuTarget) -> list[MenuEntry]:
    """The "Open with" rows for ``target``: matching launchers, then the built-ins."""
    found = items(target)
    if not found:
        return []
    b = book(target)
    default = b.default_for(found)
    out = [
        MenuEntry(
            id=f"launcher:{lc.id}",
            label=lc.label,
            group="launchers",
            shortcut="default" if default is not None and lc.id == default.id else "",
            handler=_launch(lc.id),
        )
        for lc in b.matching(found)
    ]
    refused = b.system_refusal([p for p, _ in found])
    out += [
        MenuEntry(
            id="system",
            label=SYSTEM_LABEL,
            group="system",
            shortcut="would run it" if refused else "",
            enabled=lambda t, ok=not refused: ok,
            handler=lambda call: book(call.target).open_default(paths(call.target)).note,
        ),
        MenuEntry(
            id="explorer",
            label="Show in Explorer",
            icon="",
            group="system",
            handler=lambda call: book(call.target).show_in_explorer(paths(call.target)).note,
        ),
        MenuEntry(
            id="edit",
            label="Edit launchers...",
            group="config",
            shortcut="launchers.toml",
            handler=lambda call: book(call.target).edit(),
        ),
    ]
    if b.project_path is not None:
        out.append(
            MenuEntry(
                id="edit_project",
                label="Edit project launchers...",
                group="config",
                shortcut=".specto/launchers.toml",
                handler=lambda call: book(call.target).edit_project(),
            )
        )
    return out


ENTRIES = (
    MenuEntry(
        id=DEFAULT,
        label="Open with system default",
        text=_default_label,
        group="open",
        order=10,
        visible=_default_visible,
        handler=_open_default,
    ),
    MenuEntry(
        id=OPEN_WITH,
        label="Open with",
        group="open",
        order=11,
        visible=lambda t: bool(items(t)),
        submenu=submenu,
    ),
)


def install() -> None:
    for e in ENTRIES:
        default_registry().add(e, replace=True)


__all__ = ["DEFAULT", "ENTRIES", "OPEN_WITH", "install", "submenu"]
