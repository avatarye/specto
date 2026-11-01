"""The browsers' first context-menu entries (#154): Copy path and Copy relative path.

Every view with the browser role (file tree, gallery; ADR 0020) gets them for the items
under the cursor: the absolute path with native separators, or the path relative to the
project root. Several items copy one path per line, in the target's order.
"""

from __future__ import annotations

import os

from .registry import MenuCall, MenuEntry, MenuTarget


def _relative(path: str, root: str) -> str | None:
    """``path`` relative to ``root`` (native separators), None when it is not under it."""
    if not root or not path:
        return None
    try:
        rel = os.path.relpath(path, root)
    except ValueError:  # another drive
        return None
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        return None
    return rel


def has_paths(target: MenuTarget) -> bool:
    return bool(target.items) and all(i.path for i in target.items)


def all_under_root(target: MenuTarget) -> bool:
    return has_paths(target) and all(_relative(i.path, target.root) for i in target.items)


def copy_path(call: MenuCall) -> str:
    return call.copy("\n".join(os.path.normpath(i.path) for i in call.target.items))


def copy_relative_path(call: MenuCall) -> str:
    t = call.target
    return call.copy("\n".join(_relative(i.path, t.root) or "" for i in t.items))


ENTRIES = (
    MenuEntry(
        id="copy.path",
        label="Copy path",
        icon="copy",
        group="copy",
        roles=("browser",),
        handler=copy_path,
        enabled=has_paths,
    ),
    MenuEntry(
        id="copy.relative_path",
        label="Copy relative path",
        group="copy",
        order=1,
        roles=("browser",),
        handler=copy_relative_path,
        enabled=all_under_root,
    ),
)

__all__ = ["ENTRIES", "all_under_root", "copy_path", "copy_relative_path", "has_paths"]
