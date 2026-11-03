"""The file tree's open entries (SPEC-32): "Open in its own view" (the file's dedicated
view, what a double-click opens) and "Open in contextual view" (the preview on the
tree's picks, what Ctrl+double-click opens). Each acts on one file: the row that was
right-clicked (``target.data["clicked"]``, the tree sets it) even when it sits inside a
wider selection, else the one file the target holds (the keyboard's menu on a single
pick). A folder, or several files with no clicked row, offer neither. The handler runs
the tree host's ``open_path(path, mode)`` and answers the view it opened or revealed.
Qt-free, like the registry, so the default registry carries them from the start."""

from __future__ import annotations

from .registry import MenuCall, MenuEntry, MenuTarget

DEDICATED = "file_tree.open_dedicated"
CONTEXTUAL = "file_tree.open_contextual"


def file_of(target: MenuTarget) -> str | None:
    """The one file the entries act on, as the tree keys it (its relative path), or
    None when the target is a folder or several files with no clicked row."""
    clicked = target.data.get("clicked")
    if clicked is not None:
        item = next((it for it in target.items if it.key == clicked), None)
    elif len(target.items) == 1:
        item = target.items[0]
    else:
        item = None
    if item is None or item.is_dir:
        return None
    return item.key


def _open(mode: str):
    def handler(call: MenuCall):
        path = file_of(call.target)
        if path is None or call.host is None:
            return None
        opened = call.host.open_path(path, mode)
        return opened.view if opened is not None else None

    return handler


ENTRIES = (
    MenuEntry(
        id=DEDICATED,
        label="Open in its own view",
        shortcut="Double-click",
        group="open",
        order=0,
        view_types=("file_tree",),
        visible=lambda t: file_of(t) is not None,
        handler=_open("dedicated"),
    ),
    MenuEntry(
        id=CONTEXTUAL,
        label="Open in contextual view",
        icon="eye",
        shortcut="Ctrl+Double-click",
        group="open",
        order=1,
        view_types=("file_tree",),
        visible=lambda t: file_of(t) is not None,
        handler=_open("contextual"),
    ),
)

__all__ = ["CONTEXTUAL", "DEDICATED", "ENTRIES", "file_of"]
