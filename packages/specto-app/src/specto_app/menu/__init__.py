"""The shared context menu (#154). ``registry`` (Qt-free): entries as data, targets, the
rows to show; ``paths``: the browsers' Copy path entries; ``shell``: the Qt side that
opens the shell's one menu for a view and runs the picked entry. How a view contributes:
``docs/contracts.md`` section 11."""

from __future__ import annotations

from .paths import ENTRIES as PATH_ENTRIES
from .registry import (
    GROUPS,
    ContextMenuRegistry,
    MenuCall,
    MenuEntry,
    MenuItemRef,
    MenuTarget,
    TargetKind,
)

_DEFAULT: ContextMenuRegistry | None = None


def default_registry() -> ContextMenuRegistry:
    """The app's menu: the built-in entries (Copy path, the launchers of #186), plus
    what view modules add at import."""
    global _DEFAULT
    if _DEFAULT is None:
        from ..launchers.menu import ENTRIES as LAUNCH_ENTRIES

        _DEFAULT = ContextMenuRegistry((*LAUNCH_ENTRIES, *PATH_ENTRIES))
    return _DEFAULT


__all__ = [
    "GROUPS",
    "ContextMenuRegistry",
    "MenuCall",
    "MenuEntry",
    "MenuItemRef",
    "MenuTarget",
    "TargetKind",
    "default_registry",
]
