"""The ``file_tree`` view host (spec #35 stories 3, 34, 35; ADR 0014, ADR 0022).

The view every canvas starts with. Its ``data`` input is the collection its intaker
resolved; fed from the project source (``project.all_files``, the default) that is the
project folder's root alone (#231): the tree lists one folder at a time, on expand, on
its own worker thread (``TreeLister``, over the intaker's sources and screener), and
never asks for a recursive scan of the project. Everything starts collapsed but a chain
of single-child folders from the root and the ancestors of what is selected. A tree fed
explicit files (another tree's picks) shows them as a whole, as before.

It emits one ``files`` output, ``selected_files``: what the user picked (click, Ctrl
toggles, Shift ranges; a picked folder stands for every file under it, resolved
recursively on the worker at pick time), whenever the selection changes. All files
come from the project source, not from here (#83).

A double-click on a file row opens it (``open_row`` -> ``specto_core.gestures.open_file``,
#43, ADR 0023): its dedicated view, of the type that shows the file's kind, fed that one
file by a pinned link from the project source, beside the tree; the same file again
reveals that view (``ctx.reveal_view``). Ctrl+double-click opens the contextual preview
instead (it renders the file by its kind, #106) fed by a selection intaker off this
tree's picks, new beside the tree or an existing one re-pointed, so it shows the file
and every later pick (#122), the file selected whatever the Ctrl taps toggled. The
context menu's "Open in its own view" and "Open in contextual view" do the same
(``open_path``). A double-click on a folder toggles it.
The picks are also written to ``params.selected`` (navigation): a pick arriving there
from outside (the contextual open gesture, the agent's ``canvas.open_file``) is applied,
after the folders above it are loaded.

A ``selection`` input (#168; keys are relative paths of files or folders, e.g. what a
per-pick Python link maps another tree's pick to) is shown like a pick: the tree loads
the folders above those rows, selects them, opens their ancestors, scrolls to the first
one (the QML's ``reveal``), writes them to ``params.selected`` and sends their files on
``selected_files``. The tree has no selection output, so nothing is republished as a
selection and a link back into the tree is a cycle the document refuses. Keys the tree
does not hold yet wait for its data; a pick by the user drops what is waiting.

Type-to-search (#232, ``tree_search``): a search box at the top of the tree (typing in
a focused tree starts one). While there is text the list shows the hits of the latest
keystroke instead of the tree, flat, best first, name and folder; a hit behaves like a
row (a click picks, a double-click opens, a right-click has the row's menu), its
reveal button lists the folders down to it and shows it in the tree, and Escape returns
to the tree. Everything answers on Windows when it is running and indexes the root,
else a walk of the folder; the footer says how many hits, that more exist, or which
backend found nothing.

Auto-update (#102, #115) is the tree's own: while its intaker says ``watch`` it lists
the folders it has loaded again every watch interval and applies what changed in place;
the runtime walks nothing for it. A refresh hands it its root again, which does the same
once.

Paths are POSIX, relative to the collection's root (the project folder). The rows the
QML list shows come from :class:`FileTreeModel`. Hovering an image row shows its
thumbnail (``FileTreeBridge.thumbUrl``: a ``file:`` or member URL the QML ``Image``
decodes scaled, off the GUI thread).

Each row shows the icon of its file or folder type (#122, ``file_icons``: a vendored
subset of Material Icon Theme), then the name only, a long one cut in the middle (the
start and the extension stay); a closed folder shows how many files are under it once
every folder below it has been listed (never a scan for the count). Details mode (#115;
a per-user view setting, toggled in the tree's footer, #122) adds a second line, one
row height for every row: the modified time to the second, coloured by age (under 10
min, under an hour, under a day, older; theme tokens ``age_*``), and the size. A folder's
line (#38) is never empty: its own modified time, from the directory entry its parent's
listing read (no walk), and once it is listed how many items the listing shows (``12
items``, ``empty``); an archive its file's size; the total under a folder once every
folder below it is listed; a folder with no time known yet says ``not listed yet``
(``detail_text``, ``stamp_ns``). The ages are judged against ``FileTreeModel.clock``,
read again at most once a minute (``refresh_ages``, the view's timer while details
show), and auto-update's relisting carries a folder's new time. The footer says the file
count when it is known, else how many rows are loaded, or how many files are picked.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from PySide6.QtCore import (
    Q_ARG,
    QAbstractListModel,
    QMetaObject,
    QModelIndex,
    QObject,
    QPointF,
    Qt,
    QTimer,
    Slot,
)
from PySide6.QtQuick import QQuickItem

from specto_app.hosts.base import HostContext, QuickViewHost
from specto_app.members import image_url_for
from specto_app.menu import MenuItemRef, MenuTarget
from specto_core.errors import SpectoError
from specto_core.gestures import OpenedFile, open_file
from specto_core.jobs import InlineJobManager
from specto_core.kinds import PluginManifest, ViewManifest
from specto_core.links import Collection, FilesValue, Selection
from specto_core.ports import PROJECT_ID
from specto_intake.listing import Entry, TreeLister

from . import gallery_native
from .file_icons import icon_for
from .file_tree_format import (  # noqa: F401  (re-exported: the tree's formatting)
    AGE_BOUNDS,
    age_of,
    format_bytes,
    format_count,
    format_size,
    format_stamp,
)
from .listing_worker import ListingWorker
from .tree_search import TreeSearch

FILE_TREE_QML = gallery_native.QML_DIR / "Specto" / "FileTree" / "FileTreeView.qml"
FILE_TREE_PLUGIN = PluginManifest(name="app-file-tree", version="0.1.0", views=[])
CTRL = int(Qt.KeyboardModifier.ControlModifier.value)
SHIFT = int(Qt.KeyboardModifier.ShiftModifier.value)
#: what a hover thumbnail can show (QML's image formats)
THUMB_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"})


#: a file's stat as the intaker recorded it: (size in bytes, mtime in ns); 0 = unknown
Stat = tuple[int, int]


@dataclass
class Node:
    path: str  # relative POSIX path ("" for the root)
    name: str
    is_dir: bool
    children: list[Node] = field(default_factory=list)
    size: int = 0  # a folder: the total of what is under it, once known
    mtime_ns: int = 0  # a folder: the newest file under it, once known
    #: a folder's own stat (#38), as its parent's listing saw it: the folder's modified
    #: time; an archive's file size and time. 0 when the listing had none (a tree of
    #: explicit files, a folder on the way to a source, one inside an archive)
    own_size: int = 0
    own_mtime_ns: int = 0
    #: a folder whose children have been listed (False: listed when it is expanded)
    loaded: bool = True
    archive: bool = False
    #: a folder: the files under it once every folder below it is loaded; None until then
    total: int | None = None

    def files(self) -> list[str]:
        """The files known under this node (a folder's listed ones)."""
        if not self.is_dir:
            return [self.path]
        out: list[str] = []
        for c in self.children:
            out.extend(c.files())
        return out

    @property
    def complete(self) -> bool:
        """Every file under this node is known."""
        return not self.is_dir or self.total is not None


def _recount(node: Node) -> None:
    """A folder's total, size and newest time from its children (None while a folder
    below it is not loaded yet)."""
    if not node.is_dir:
        return
    if not node.loaded:
        node.total = None
        return
    total, size, mtime = 0, 0, 0
    for c in node.children:
        if c.is_dir:
            if c.total is None:
                node.total = None
                return
            total += c.total
        else:
            total += 1
        size += c.size
        mtime = max(mtime, c.mtime_ns)
    node.total, node.size, node.mtime_ns = total, size, mtime


def build_tree(paths: list[str], stats: dict[str, Stat] | None = None) -> Node:
    """A whole tree from a complete listing: folders first, then files, each sorted
    case-insensitively. ``stats`` (path -> (size, mtime_ns)) sizes and dates the files;
    every folder sums the sizes, keeps the newest time and counts its files."""
    root = Node("", "", True)
    dirs: dict[str, Node] = {"": root}
    stats = stats or {}
    for p in sorted(set(paths)):
        parts = p.split("/")
        size, mtime_ns = stats.get(p, (0, 0))
        parent = root
        prefix = ""
        for part in parts[:-1]:
            prefix = f"{prefix}/{part}" if prefix else part
            node = dirs.get(prefix)
            if node is None:
                node = dirs[prefix] = Node(prefix, part, True)
                parent.children.append(node)
            parent = node
        parent.children.append(Node(p, parts[-1], False, size=size, mtime_ns=mtime_ns))
    for node in dirs.values():
        node.children.sort(key=lambda n: (not n.is_dir, n.name.lower()))
    for node in sorted(dirs.values(), key=lambda n: -n.path.count("/") - (1 if n.path else 0)):
        _recount(node)
    return root


def stamp_ns(node: Node) -> int:
    """The time a row's details line shows: a file's modified time; a folder's own
    (#38) when the listing saw it, else the newest file under it once every folder
    below it is listed; 0 when none is known."""
    if not node.is_dir:
        return node.mtime_ns
    if node.own_mtime_ns:
        return node.own_mtime_ns
    return node.mtime_ns if node.complete else 0


def detail_text(node: Node) -> str:
    """What follows the time on a row's details line: a file's size; a folder's item
    count once it is listed (``12 items``, ``empty``: what the listing shows, so what
    the screener drops is not counted), an archive's own size, the total under a folder
    once every folder below it is listed. A folder nothing is known about yet (no own
    time: one on the way to a source outside the project, a folder inside an archive
    not opened yet) says ``not listed yet``, so no row's line is empty (#38)."""
    if not node.is_dir:
        return format_bytes(node.size)
    parts: list[str] = []
    if node.loaded:
        parts.append(format_count(len(node.children)))
    if node.archive and node.own_size:
        parts.append(format_bytes(node.own_size))
    elif node.complete and node.size:
        parts.append(format_bytes(node.size))
    if not parts and not stamp_ns(node):
        return "not listed yet"
    return " · ".join(parts)


def _stat_key(node: Node) -> tuple[bool, int, int, int, int]:
    """What a listing landing again compares, per child: a file's stat, a folder's own
    (a folder touched since is a change, so its details line follows)."""
    return (node.is_dir, node.size, node.mtime_ns, node.own_size, node.own_mtime_ns)


def default_expanded(root: Node) -> set[str]:
    """What opens on its own (#231): the chain of single-child folders down from the
    root, and nothing else."""
    out: set[str] = set()
    node = root
    while len(node.children) == 1 and node.children[0].is_dir:
        node = node.children[0]
        out.add(node.path)
    return out


def ancestors(path: str) -> list[str]:
    """The folders above a tree path, top down (the root, ``""``, first)."""
    parts = path.split("/")
    return [""] + ["/".join(parts[:i]) for i in range(1, len(parts))]


class FileTreeModel(QAbstractListModel):
    """The visible rows of the tree (depth-first, expanded folders only), over nodes
    that are listed one folder at a time (``load``) or all at once (``set_paths``)."""

    (
        PathRole,
        NameRole,
        DepthRole,
        IsDirRole,
        ExpandedRole,
        SelectedRole,
        CountRole,
        SizeRole,
        StampRole,
        AgeRole,
        IconRole,
    ) = range(Qt.ItemDataRole.UserRole + 1, Qt.ItemDataRole.UserRole + 12)
    ROLES = {
        PathRole: b"path",
        NameRole: b"name",
        DepthRole: b"depth",
        IsDirRole: b"isDir",
        ExpandedRole: b"expanded",
        SelectedRole: b"selected",
        CountRole: b"count",
        SizeRole: b"sizeText",
        StampRole: b"stamp",
        AgeRole: b"age",
        IconRole: b"icon",
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.root = Node("", "", True, total=0)
        self.expanded: set[str] = set()
        self.selected: set[str] = set()
        self._rows: list[tuple[Node, int]] = []
        self._nodes: dict[str, Node] = {}
        self._parents: dict[str, Node] = {}
        #: what the ages are judged against (a fake clock in tests), read on new data and
        #: on ``refresh_ages``
        self.clock: Callable[[], float] = time.time
        self.now = self.clock()

    # -- data ----------------------------------------------------------------------------
    def set_paths(self, paths: list[str], stats: dict[str, Stat] | None = None) -> None:
        """The whole tree at once (a collection of explicit files): every folder loaded."""
        self.root = build_tree(paths, stats)
        self._index()
        known = set(self._nodes)
        self.expanded = (self.expanded & known) or default_expanded(self.root)
        self.selected &= known
        self.now = self.clock()
        self._set_rows(self._walk())

    def reset(self) -> None:
        """An empty tree whose root is yet to be listed."""
        self.root = Node("", "", True, loaded=False)
        self._nodes = {}
        self._parents = {}
        self.expanded = set()
        self.selected = set()
        self._set_rows([])

    def _index(self) -> None:
        self._nodes = {}
        self._parents = {}
        stack = [self.root]
        while stack:
            n = stack.pop()
            for c in n.children:
                self._nodes[c.path] = c
                self._parents[c.path] = n
                stack.append(c)

    def node(self, path: str) -> Node | None:
        return self.root if path == "" else self._nodes.get(path)

    def load(self, path: str, entries: list[Entry]) -> bool:
        """One folder's listing landed: its children become ``entries`` (a child already
        there keeps its subtree and its open state), in place. True when anything
        changed (an entry came, went, or its stat moved)."""
        node = self.node(path)
        if node is None or not node.is_dir:
            return False
        before = {c.path: _stat_key(c) for c in node.children}
        old = {c.path: c for c in node.children}
        children: list[Node] = []
        for e in entries:
            c = old.get(e.path)
            if c is not None and c.is_dir == e.is_dir:
                c.name, c.archive = e.name, e.archive
            else:
                if c is not None:
                    self._forget(c)
                c = Node(e.path, e.name, e.is_dir, loaded=not e.is_dir, archive=e.archive)
            if e.is_dir:
                c.own_size, c.own_mtime_ns = e.size, e.mtime_ns  # its own stat (#38)
            else:
                c.size, c.mtime_ns = e.size, e.mtime_ns
            children.append(c)
            self._nodes[c.path] = c
            self._parents[c.path] = node
        for p, c in old.items():
            if p not in {e.path for e in entries}:
                self._forget(c)
        children.sort(key=lambda n: (not n.is_dir, n.name.lower()))
        changed = not node.loaded or before != {c.path: _stat_key(c) for c in children}
        node.children = children
        node.loaded = True
        self._recount_up(node)
        self.now = self.clock()
        self._set_rows(self._walk())
        return changed

    def _forget(self, node: Node) -> None:
        for p in [node.path, *node.files()] + [c.path for c in self._descendants(node)]:
            self._nodes.pop(p, None)
            self._parents.pop(p, None)
            self.expanded.discard(p)
            self.selected.discard(p)

    def _descendants(self, node: Node) -> list[Node]:
        out: list[Node] = []
        stack = list(node.children)
        while stack:
            n = stack.pop()
            out.append(n)
            stack.extend(n.children)
        return out

    def _recount_up(self, node: Node) -> None:
        while node is not None:
            _recount(node)
            node = self._parents.get(node.path) if node.path else None  # type: ignore[assignment]

    def loaded_dirs(self) -> list[str]:
        """Every folder listed so far, the root first."""
        out = [""] if self.root.loaded else []
        out.extend(p for p, n in self._nodes.items() if n.is_dir and n.loaded and not n.archive)
        out.extend(p for p, n in self._nodes.items() if n.is_dir and n.loaded and n.archive)
        return out

    def unloaded_ancestors(self, paths: list[str]) -> list[str]:
        """The folders above ``paths`` not listed yet, nearest the root first; a folder
        the tree does not hold stops the search for that path (its parent is listed and
        does not have it: the path is not there)."""
        out: dict[str, None] = {}
        for p in paths:
            for a in ancestors(p):
                n = self.node(a)
                if n is None or not n.is_dir:
                    break
                if not n.loaded:
                    out[a] = None
                    break
        return list(out)

    @property
    def complete(self) -> bool:
        """Every file of the tree is known (every folder listed)."""
        return self.root.complete

    def _set_rows(self, rows: list[tuple[Node, int]]) -> None:
        """Move to ``rows`` as row inserts and removes, keyed by path, then refresh every
        row's text: a model reset would scroll the list back to the top (#110)."""
        old = [n.path for n, _ in self._rows]
        new = [n.path for n, _ in rows]
        ops = SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
        for tag, i1, i2, j1, j2 in reversed(ops):  # from the end: earlier rows keep their index
            if tag == "equal":
                continue
            if i2 > i1:
                self.beginRemoveRows(QModelIndex(), i1, i2 - 1)
                del self._rows[i1:i2]
                self.endRemoveRows()
            if j2 > j1:
                self.beginInsertRows(QModelIndex(), i1, i1 + j2 - j1 - 1)
                self._rows[i1:i1] = rows[j1:j2]
                self.endInsertRows()
        self._rows = rows
        if rows:
            self.dataChanged.emit(self.index(0), self.index(len(rows) - 1))

    def _walk(self) -> list[tuple[Node, int]]:
        """The visible rows: depth-first, into expanded folders only."""
        rows: list[tuple[Node, int]] = []

        def walk(node: Node, depth: int) -> None:
            for c in node.children:
                rows.append((c, depth))
                if c.is_dir and c.path in self.expanded:
                    walk(c, depth + 1)

        walk(self.root, 0)
        return rows

    def rows(self) -> list[dict[str, Any]]:
        return [
            {
                "path": n.path,
                "name": n.name,
                "depth": d,
                "is_dir": n.is_dir,
                "expanded": n.path in self.expanded,
                "selected": n.path in self.selected,
                "size": detail_text(n),
                "stamp": format_stamp(stamp_ns(n)),
                "age": age_of(stamp_ns(n), self.now),
                "icon": self.icon_of(n),
            }
            for n, d in self._rows
        ]

    def icon_of(self, node: Node) -> str:
        """The row's icon (#122): by the file's name and type, the folder's name and
        whether it is open."""
        return icon_for(node.name, is_dir=node.is_dir, expanded=node.path in self.expanded)

    def all_files(self) -> list[str]:
        """Every file known so far (the listed folders')."""
        return self.root.files()

    def selected_files(self) -> list[str]:
        """The files the picks stand for, as far as they are known: a picked folder's
        listed files (see ``selection_complete``)."""
        out: set[str] = set()
        for p in self.selected:
            node = self._nodes.get(p)
            if node is not None:
                out.update(node.files())
        return sorted(out)

    def selection_complete(self) -> bool:
        """Every picked folder is fully listed, so ``selected_files`` is all of them."""
        return all(n is None or n.complete for n in (self._nodes.get(p) for p in self.selected))

    def reveal(self, paths: list[str]) -> bool:
        """Select ``paths`` (the ones the tree holds) and open every folder above them;
        True when the selection or the open folders changed."""
        known = [p for p in paths if p in self._nodes]
        opened = set(self.expanded)
        for p in known:
            opened.update(a for a in ancestors(p) if a)
        if set(known) == self.selected and opened == self.expanded:
            return False
        self.selected = set(known)
        if opened != self.expanded:
            self.expanded = opened
            self._set_rows(self._walk())
        else:
            self._changed_rows()
        return True

    def in_tree_order(self, paths: set[str]) -> list[str]:
        """``paths`` in the order the tree lists them (folders first, then names, depth
        first), open or not; unknown paths dropped."""
        out: list[str] = []
        stack = list(reversed(self.root.children))
        while stack:
            n = stack.pop()
            if n.path in paths:
                out.append(n.path)
            stack.extend(reversed(n.children))
        return out

    def stats_of(self, paths: list[str]) -> dict[str, list[int]]:
        """``path -> [size, mtime_ns]`` for the files whose stat the tree holds."""
        out: dict[str, list[int]] = {}
        for p in paths:
            n = self._nodes.get(p)
            if n is not None and not n.is_dir and n.mtime_ns:
                out[p] = [n.size, n.mtime_ns]
        return out

    def row_of(self, path: str) -> int:
        return next((i for i, (n, _) in enumerate(self._rows) if n.path == path), -1)

    # -- gestures ------------------------------------------------------------------------
    def toggle(self, row: int) -> bool:
        if not 0 <= row < len(self._rows):
            return False
        node, _ = self._rows[row]
        if not node.is_dir:
            return False
        if node.path in self.expanded:
            self.expanded.discard(node.path)
        else:
            self.expanded.add(node.path)
        self._set_rows(self._walk())
        return True

    def expand(self, path: str) -> bool:
        """Open a folder by path (and the folders above it); False when not held."""
        node = self._nodes.get(path)
        if node is None or not node.is_dir:
            return False
        wanted = {a for a in ancestors(path) if a} | {path}
        if wanted <= self.expanded:
            return False
        self.expanded |= wanted
        self._set_rows(self._walk())
        return True

    def select(self, row: int, modifiers: int, anchor: int) -> bool:
        """Click semantics; returns True when the selection changed."""
        if not 0 <= row < len(self._rows):
            return False
        before = set(self.selected)
        path = self._rows[row][0].path
        if modifiers & SHIFT and 0 <= anchor < len(self._rows):
            lo, hi = sorted((anchor, row))
            rng = {n.path for n, _ in self._rows[lo : hi + 1]}
            self.selected = (self.selected | rng) if modifiers & CTRL else rng
        elif modifiers & CTRL:
            self.selected ^= {path}
        else:
            self.selected = {path}
        if self.selected == before:
            return False
        self._changed_rows()
        return True

    def refresh_ages(self) -> None:
        """Read the clock again and tell the list the ages changed: only the rows it
        shows ask again, for the age alone."""
        self.now = self.clock()
        if self._rows:
            self.dataChanged.emit(self.index(0), self.index(len(self._rows) - 1), [self.AgeRole])

    def clear_selection(self) -> bool:
        if not self.selected:
            return False
        self.selected = set()
        self._changed_rows()
        return True

    def _changed_rows(self) -> None:
        if self._rows:
            self.dataChanged.emit(
                self.index(0), self.index(len(self._rows) - 1), [self.SelectedRole]
            )

    # -- QAbstractListModel ---------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self) -> dict[int, bytes]:  # type: ignore[override]
        return dict(self.ROLES)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        node, depth = self._rows[index.row()]
        if role in (self.NameRole, Qt.ItemDataRole.DisplayRole):
            return node.name
        if role == self.PathRole:
            return node.path
        if role == self.DepthRole:
            return depth
        if role == self.IsDirRole:
            return node.is_dir
        if role == self.ExpandedRole:
            return node.path in self.expanded
        if role == self.SelectedRole:
            return node.path in self.selected
        if role == self.CountRole:
            return (node.total or 0) if node.is_dir else 0
        if role == self.SizeRole:
            return detail_text(node)
        if role == self.StampRole:
            return format_stamp(stamp_ns(node))
        if role == self.AgeRole:
            return age_of(stamp_ns(node), self.now)
        if role == self.IconRole:
            return self.icon_of(node)
        return None


class FileTreeBridge(QObject):
    """Handed to ``FileTreeView.qml`` as ``bridge``: the gestures, by row."""

    def __init__(self, host: FileTreeHost) -> None:
        super().__init__()
        self._host = host

    @Slot(int, int)
    def click(self, row: int, modifiers: int) -> None:
        self._host.click(int(row), int(modifiers))

    @Slot(int)
    def toggle(self, row: int) -> None:
        self._host.toggle(int(row))

    @Slot(int, int)
    def open(self, row: int, modifiers: int) -> None:
        """Double-click on a file row (#43): its dedicated view; with Ctrl the contextual
        preview."""
        self._host.open_row(int(row), int(modifiers))

    @Slot()
    def clearSelection(self) -> None:
        self._host.clear_selection()

    @Slot()
    def refreshAges(self) -> None:
        """The details line's timer (once a minute): the ages against the clock now."""
        self._host.model.refresh_ages()

    @Slot(int, float, float)
    def contextMenu(self, row: int, x: float, y: float) -> None:
        """Right-click on a row at a window point: its context menu (#154)."""
        self._host.open_context_menu({"kind": "items", "row": int(row)}, float(x), float(y))

    @Slot(int)
    def drag(self, row: int) -> None:
        """A left drag left the row: drag it out, or the selection it is in (#185)."""
        self._host.start_drag({"kind": "items", "row": int(row)})

    @Slot(str, result=str)
    def thumbUrl(self, path: str) -> str:
        """An ``Image`` source for the hover thumbnail of an image row; "" otherwise."""
        return self._host.thumb_url(str(path))

    # -- type-to-search (#232) -------------------------------------------------------------
    @Slot(str)
    def search(self, text: str) -> None:
        """A keystroke in the search box: the latest text's hits, and only those."""
        self._host.search.query(str(text))

    @Slot(int, int)
    def clickHit(self, row: int, modifiers: int) -> None:
        self._host.search.click(int(row), int(modifiers))

    @Slot(int, int)
    def openHit(self, row: int, modifiers: int) -> None:
        self._host.search.open(int(row), int(modifiers))

    @Slot(int)
    def revealHit(self, row: int) -> None:
        """Back to the tree at the hit: its folders listed, the row shown and selected."""
        self._host.search.reveal(int(row))

    @Slot(int, float, float)
    def hitContextMenu(self, row: int, x: float, y: float) -> None:
        self._host.open_context_menu({"kind": "hit", "row": int(row)}, float(x), float(y))


class FileTreeHost(QuickViewHost):
    """One live file tree view; see the module docstring."""

    def __init__(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> None:
        super().__init__(ctx, view_id, manifest)
        self.model = FileTreeModel(self)
        self.bridge = FileTreeBridge(self)
        self.root = ""
        self.collection: Collection | None = None
        #: the folder-at-a-time lister (#231) when the data is the project folder's
        #: root; None for a tree fed explicit files
        self.lister: TreeLister | None = None
        self.worker = ListingWorker(
            inline=isinstance(ctx.engine.jobs, InlineJobManager), parent=self
        )
        #: folders whose listing is in flight
        self._loading: set[str] = set()
        #: the folder whose listing continues the single-child chain from the root
        self._chain: str | None = None
        #: a received selection's keys the tree did not hold yet (#168): applied when
        #: its data lands, dropped by the user's own pick
        self._wanted: list[str] | None = None
        #: the last selection received: the same again (the link re-resolving when this
        #: tree's data changes) is not shown again over the user's own pick since
        self._received: Selection | None = None
        #: rows to select and show once the folders above them are listed (a restored
        #: pick, ``params.selected`` from outside, a tool's ``select_paths``)
        self._pending: list[str] | None = None
        self._pending_expand: str | None = None
        #: the folder pick in flight (its worker token) and the files last sent
        self._pick_token: int | None = None
        self._emitted: list[str] = []
        #: a listing of the loaded folders is in flight (set before the submit: an
        #: inline worker answers inside it)
        self._relisting = False
        self._anchor = -1
        self._watch = QTimer(self)
        self._watch.timeout.connect(self._relist_loaded)
        #: type-to-search (#232): the hits model and the query in flight
        self.search = TreeSearch(self)
        if ctx.qml_engine is not None:
            gallery_native.ensure_import_path(ctx.qml_engine)
            self.item = ctx.create_item(
                FILE_TREE_QML,
                properties={
                    "viewId": view_id,
                    "bridge": self.bridge,
                    "treeModel": self.model,
                    "searchModel": self.search.model,
                },
            )
        self.set_health("paused", "no data")

    # -- ports -------------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        if port == "selection":
            if isinstance(value, Selection) and value != self._received:
                self._received = value
                self._wanted = list(value.keys)
                self._apply_wanted()
            return
        if port != "data":
            return
        self.collection = value if isinstance(value, Collection) else None
        if self.collection is None:
            self.root = self.root or (
                str(self.ctx.project_root) if self.ctx.project_root is not None else ""
            )
            self.lister = None
            self._watch.stop()
            self.worker.cancel_all()
            self._loading.clear()
            self.model.set_paths([])
            self.set_health("paused", "no data")
            self._caption()
            self._emit_selected()
            return
        link = self.ctx.live.data["links"].get(self.collection.link)
        if self._lazy_link(link) and not self.collection.items:
            self._receive_root(self.collection, link)
        else:
            self._receive_files(self.collection)
        if self._wanted is not None and self._apply_wanted():
            return  # the received selection went out with its files
        self._caption()
        if self._pending is None:  # else the restored picks go out once their folders are listed
            self._emit_selected()

    @staticmethod
    def _lazy_link(link: dict[str, Any] | None) -> bool:
        """The intaker from the project source (#231): the tree lists folders itself."""
        return (
            link is not None
            and link.get("kind") == "intaker"
            and link.get("source", {}).get("view") == PROJECT_ID
        )

    def _receive_root(self, col: Collection, link: dict[str, Any]) -> None:
        """The project folder's root: list it (and list the loaded folders again when
        it is the same root handed again: a refresh, a changed link)."""
        params = dict(link.get("params") or {})
        spec = {"sources": list(params.get("sources") or ["."])}
        if params.get("screener"):
            spec["screener"] = list(params["screener"])
        same = self.lister is not None and self.root == col.root
        self.lister = TreeLister(col.root, spec, self.ctx.links.env.get("aliases") or {})
        self.root = col.root
        interval = max(0.05, float(getattr(self.ctx.links, "watch_interval_s", 1.0)))
        self._watch.setInterval(int(interval * 1000))
        if params.get("watch") and self.root:
            self._watch.start()
        else:
            self._watch.stop()
        if same:
            self._relist_loaded()
            return
        self.worker.cancel_all()
        self._loading.clear()
        self._relisting = False
        self.model.reset()
        self.search.clear()
        self._chain = ""
        self.set_health("loading", "listing")
        if not self.model.selected:
            # a reopened project: the picks remembered in params come back
            stored = self.params.get("selected")
            if isinstance(stored, list) and stored:
                self._pending = [str(p) for p in stored]
        self._request("")

    def _receive_files(self, col: Collection) -> None:
        """Explicit files (another tree's picks, a collection handed to the tree): the
        whole tree at once."""
        self.lister = None
        self._watch.stop()
        self.worker.cancel_all()
        self._loading.clear()
        self.root = col.root
        if not self.model.selected:
            stored = self.params.get("selected")
            if isinstance(stored, list):
                self.model.selected = {str(p) for p in stored}
        paths, stats = self._paths_of(col)
        self.model.set_paths(paths, stats)
        self._health()

    def _request(self, path: str) -> None:
        """List one folder on the worker, once."""
        lister = self.lister
        if lister is None or path in self._loading:
            return
        self._loading.add(path)
        self.worker.submit(
            lambda cancelled: lister.list_dir(path),
            lambda entries, error: self._listed(path, entries, error),
        )

    def _listed(self, path: str, entries: list[Entry] | None, error: str | None) -> None:
        self._loading.discard(path)
        if error is not None:
            self.set_health("error", error.splitlines()[0])
            return
        if entries is None or self.lister is None:
            return
        self.model.load(path, entries)
        if self._chain is not None and path == self._chain:
            # the chain of single-child folders from the root opens on its own
            node = self.model.node(path)
            if node is not None and len(node.children) == 1 and node.children[0].is_dir:
                child = node.children[0]
                self._chain = child.path
                self.model.expand(child.path)
                if not child.loaded:
                    self._request(child.path)
            else:
                self._chain = None
        self._continue_pending()
        self._health()
        self._caption()

    @Slot()
    def _relist_loaded(self) -> None:
        """Auto-update (#102) and refresh: list every loaded folder again on the worker
        and apply what changed in place; picks go out again when their files moved."""
        if getattr(self.ctx.engine, "closed", False):
            self._watch.stop()  # a host outliving its engine (tests) follows nothing
            return
        lister = self.lister
        if lister is None or self._relisting or not self.model.root.loaded:
            return
        dirs = self.model.loaded_dirs()

        def job(cancelled: Callable[[], bool]) -> list[tuple[str, list[Entry]]]:
            out = []
            for p in dirs:
                if cancelled():
                    break
                out.append((p, lister.list_dir(p)))
            return out

        self._relisting = True
        self.worker.submit(job, self._relisted)

    def _relisted(self, results: list[tuple[str, list[Entry]]] | None, error: str | None) -> None:
        self._relisting = False
        if results is None or self.lister is None:
            return
        changed = False
        for path, entries in results:
            node = self.model.node(path)
            if node is not None and node.loaded:
                changed = self.model.load(path, entries) or changed
        if changed:
            self._continue_pending()
            self._emit_selected()
            self._caption()
            self._health()

    def _health(self) -> None:
        n = len(self.model.all_files())
        if self.model.complete:
            self.set_health("ok", f"{n} files")
        else:
            self.set_health("ok", f"{self.model.rowCount()} rows listed")

    # -- revealing rows whose folders are not listed yet -----------------------------------
    def _continue_pending(self) -> None:
        """Load the next folders above the rows waiting to be shown; once they are all
        listed (or found missing), show them."""
        self._continue_expand()
        if self._wanted is not None and self._apply_wanted():
            return
        if self._pending is None:
            return
        missing = self.model.unloaded_ancestors(self._pending)
        if missing:
            for p in missing:
                self._request(p)
            return
        paths, self._pending = self._pending, None
        self._show(paths)

    def _show(self, paths: list[str]) -> None:
        """Select the rows (the ones the tree holds), open their folders, scroll to the
        first one, remember and send the picks."""
        self.model.reveal(paths)
        first = next((k for k in paths if k in self.model._nodes), None)
        if first is not None and self.item is not None:
            QMetaObject.invokeMethod(
                self.item, "reveal", Q_ARG("QVariant", self.model.row_of(first))
            )
        self._emit_selected()
        self._remember_selected()
        self._caption()

    def _apply_wanted(self) -> bool:
        """Show the received selection (``_wanted``): load the folders above its keys,
        select and reveal them, scroll to the first key, remember it and send its
        files. Nothing until the tree holds data; keys it does not hold wait for the
        next data. True when it went out."""
        wanted = self._wanted
        if wanted is None or self.collection is None:
            return False
        if self.lister is not None:
            if not self.model.root.loaded:
                return False
            missing = self.model.unloaded_ancestors(wanted)
            if missing:
                for p in missing:
                    self._request(p)
                return False
        if all(k in self.model._nodes for k in wanted):
            self._wanted = None
        self._show(wanted)
        return True

    def _paths_of(self, col: Collection) -> tuple[list[str], dict[str, Stat]]:
        """The collection's files as tree paths, with the stat the intaker recorded per
        part (``meta.stat[role] = [size, mtime_ns]``): nothing is stat'ed here."""
        root = os.path.normcase(os.path.abspath(col.root))
        out: list[str] = []
        stats: dict[str, Stat] = {}
        for it in col.items:
            rels = it.meta.get("rel") or {}
            per_part = it.meta.get("stat") or {}
            for role, path in it.parts.items():
                rel = rels.get(role)
                if rel and os.path.normcase(os.path.abspath(it.meta.get("root", col.root))) == root:
                    tree_path = rel.replace("\\", "/")
                else:
                    ap = os.path.abspath(path)
                    if os.path.normcase(ap).startswith(root + os.sep):
                        tree_path = os.path.relpath(ap, col.root).replace("\\", "/")
                    else:
                        tree_path = ap.replace("\\", "/")
                out.append(tree_path)
                st = per_part.get(role)
                if isinstance(st, list | tuple) and len(st) == 2:
                    stats[tree_path] = (int(st[0]), int(st[1]))
        return out, stats

    # -- the picks -----------------------------------------------------------------------------
    def _emit_selected(self, hits: dict[str, Stat] | None = None) -> None:
        """Send the picks' files. Files, and folders the tree has fully listed, go out
        at once with the stat the tree holds (so the intakers off them do not stat the
        files again, and the same picks unchanged resolve nothing, #76), as does a
        search hit whose stat the search saw (``hits``); a folder not fully listed, or
        a path the tree does not hold, is resolved to its files on the worker first,
        and only the latest pick's answer goes out."""
        if self._pick_token is not None:
            self.worker.cancel(self._pick_token)
            self._pick_token = None
        picks = sorted(self.model.selected)
        unknown = [p for p in picks if p not in self.model._nodes]
        from_hits = {p: hits[p] for p in unknown if hits is not None and p in hits}
        settled = self.model.selection_complete() and len(from_hits) == len(unknown)
        if self.lister is not None and not settled:
            lister = self.lister
            token = self.worker.submit(
                lambda cancelled: lister.files_under(picks, cancelled),
                self._picked,
            )
            if self.worker.is_pending(token):  # an inline worker answered already
                self._pick_token = token
            return
        files = sorted(set(self.model.selected_files()) | set(from_hits))
        stats = self.model.stats_of(files)
        stats.update({p: list(st) for p, st in from_hits.items()})
        self._send(files, stats)

    def _picked(self, result: tuple[list[str], dict[str, Stat]] | None, error: str | None) -> None:
        self._pick_token = None
        if result is None:
            return
        paths, stats = result
        self._send(paths, {p: list(st) for p, st in stats.items()})
        self._caption()

    def _send(self, paths: list[str], stats: dict[str, list[int]]) -> None:
        self._emitted = paths
        self.emit("selected_files", FilesValue(root=self.root, paths=paths, stats=stats))

    def _remember_selected(self) -> None:
        """The picked rows (files or folders) into ``params.selected`` (navigation, no
        undo step) so a reopened project picks them again."""
        self.ctx.live.remember(self.view_id, {"selected": sorted(self.model.selected)})

    # -- gestures --------------------------------------------------------------------------------
    def click(self, row: int, modifiers: int = 0) -> bool:
        self._wanted = None  # the user's own pick wins over a received one still waiting
        self._pending = None
        changed = self.model.select(row, modifiers, self._anchor)
        if not modifiers & SHIFT:
            self._anchor = row
        if changed:
            self._emit_selected()
            self._remember_selected()
            self._caption()
        return changed

    def toggle(self, row: int) -> bool:
        changed = self.model.toggle(row)
        if changed and 0 <= row < len(self.model._rows):
            node, _ = self.model._rows[row]
            if node.path in self.model.expanded and not node.loaded:
                self._request(node.path)
        return changed

    def expand_path(self, path: str) -> None:
        """Open a folder by path, listing the folders down to it first (tools, tests)."""
        self._pending_expand = path
        self._continue_expand()

    def _continue_expand(self) -> None:
        path = self._pending_expand
        if path is None:
            return
        missing = self.model.unloaded_ancestors([path])
        if missing:
            for p in missing:
                self._request(p)
            return
        self._pending_expand = None
        node = self.model.node(path)
        if node is not None and node.is_dir:
            self.model.expand(path)
            if not node.loaded:
                self._request(path)

    def open_row(self, row: int, modifiers: int = 0) -> OpenedFile | None:
        """Double-click on a row (#43): a file opens in its dedicated view, or, with
        Ctrl, in the contextual preview (``open_path``); a folder is not opened here
        (None)."""
        if not 0 <= row < len(self.model._rows):
            return None
        node, _ = self.model._rows[row]
        if node.is_dir:
            return None
        return self.open_path(node.path, "contextual" if modifiers & CTRL else "dedicated")

    def open_path(self, path: str, mode: str) -> OpenedFile | None:
        """Open a file of this tree the way ``mode`` says (``gestures.open_file``: one
        user submit, or a pick in this tree, or nothing when the file's dedicated view
        is there already, in which case the shell reveals it). The context menu's "Open
        in its own view" and "Open in contextual view" come here too."""
        try:
            opened = open_file(
                self.ctx.live,
                self.view_id,
                path,
                mode=mode,
                prefer=self.ctx.selected_views(),
                plugins=self.ctx.engine.plugins,
            )
        except SpectoError as e:
            self.ctx.notice(e, "open file")
            return None
        if opened.action == "focused":
            self.ctx.reveal_view(opened.view)
        return opened

    def clear_selection(self) -> bool:
        if self.model.clear_selection():
            self._emit_selected()
            self._remember_selected()
            self._caption()
            return True
        return False

    def select_paths(self, paths: list[str]) -> None:
        """Select by path (tools, tests, ``params.selected`` from outside): the folders
        above are listed first; paths the tree does not hold are ignored."""
        if self.lister is not None and self.model.unloaded_ancestors(list(paths)):
            self._pending = list(paths)
            self._continue_pending()
            return
        self.model.selected = {p for p in paths if p in self.model._nodes}
        self.model._changed_rows()
        self._emit_selected()
        self._caption()

    def load_all(self) -> None:
        """List every folder now, on this thread (tools, tests): the whole tree known."""
        if self.lister is None:
            return
        paths, stats = self.lister.files_under([""])
        self.model.set_paths(paths, stats)
        self._health()
        self._caption()

    def thumb_url(self, path: str) -> str:
        node = self.model._nodes.get(path)
        if node is None or node.is_dir or not self.root:
            return ""
        if os.path.splitext(path)[1].lower() not in THUMB_EXTS:
            return ""
        full = path if os.path.isabs(path) else os.path.join(self.root, *path.split("/"))
        return image_url_for(full)

    # -- context menu (#154) -------------------------------------------------------------------
    def context_target(self, hit: dict[str, Any] | None) -> MenuTarget | None:
        """A right-clicked row inside the selection stands for every selected row; one
        outside it for itself alone (no pick: a right-click changes nothing). The
        keyboard's target (``hit`` None) is the selection."""
        clicked: dict[str, Any] = {}  # the row under the cursor, for the open entries (#43)
        if hit is not None and hit.get("kind") == "items":
            row = int(hit.get("row", -1))
            if not 0 <= row < len(self.model._rows):
                return None
            path = self.model._rows[row][0].path
            paths = self.model.selected if path in self.model.selected else {path}
            clicked = {"clicked": path}
        elif hit is not None and hit.get("kind") == "hit":
            # a search hit (#232): the hits' order, then any picked rows the search hides
            found = self.search.hit(int(hit.get("row", -1)))
            if found is None:
                return None
            paths = self.model.selected if found.path in self.model.selected else {found.path}
            shown = [h.path for h in self.search.model.hits if h.path in paths]
            rest = [p for p in self.model.in_tree_order(set(paths)) if p not in shown]
            items = [self._menu_item(p) for p in shown + rest]
            return self.menu_target(
                "items" if items else "view", items, data={"clicked": found.path}
            )
        elif hit is None:
            paths = self.model.selected
        else:
            return super().context_target(hit)
        items = [self._menu_item(p) for p in self.model.in_tree_order(set(paths))]
        return self.menu_target("items" if items else "view", items, data=clicked)

    def _menu_item(self, path: str) -> MenuItemRef:
        node = self.model._nodes.get(path)
        if os.path.isabs(path):  # a file outside the collection's root
            full = os.path.normpath(path)
        else:
            full = os.path.normpath(os.path.join(self.root, *path.split("/")))
        return MenuItemRef(key=path, path=full, is_dir=node.is_dir if node is not None else False)

    def context_anchor(self, target: MenuTarget) -> tuple[float, float] | None:
        """Under the first target row when the list shows it, else the default."""
        lst = (
            self.item.findChild(QQuickItem, f"fileTreeList:{self.view_id}")
            if self.item is not None
            else None
        )
        row = self.model.row_of(target.items[0].key) if target.items else -1
        if lst is None or row < 0:
            return super().context_anchor(target)
        row_h = float(self.item.property("rowH") or 20)
        y = row * row_h - float(lst.property("contentY") or 0) + row_h
        if not 0 <= y <= lst.height():
            return super().context_anchor(target)
        p = lst.mapToScene(QPointF(24, y))
        return p.x(), p.y()

    # -- contract ------------------------------------------------------------------------------
    def on_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        """``params.selected`` written from outside (the open-file gesture picking a file
        here, a reopened project) becomes the tree's selection."""
        prefix = f"/views/{self.view_id}"
        paths = [str(op.get("path", "")) for op in patch]
        if not any(p == prefix or p.startswith(prefix + "/params") for p in paths):
            return
        stored = self.params.get("selected")
        if not isinstance(stored, list):
            return
        wanted = {str(p) for p in stored}
        if wanted != self.model.selected:
            self.select_paths(sorted(wanted))

    def rows(self) -> list[dict[str, Any]]:
        return self.model.rows()

    def snapshot(self) -> dict[str, Any]:
        return {
            **super().snapshot(),
            "root": self.root,
            "files": len(self.model.all_files()),
            "complete": self.model.complete,
            "rows": len(self.model.rows()),
            "expanded": sorted(self.model.expanded),
            "loaded": sorted(self.model.loaded_dirs()),
            "selected": list(self._emitted),
            "selected_rows": sorted(self.model.selected),
            "search": self.search.snapshot(),
        }

    def _caption(self) -> None:
        if self.item is None:
            return
        n = len(self.model.all_files())
        complete = self.model.complete
        if self.model.selected:
            # the picked files once known (the worker's answer), else the picked rows
            k = (
                len(self.model.selected_files())
                if self.model.selection_complete()
                else max(len(self._emitted), len(self.model.selected))
            )
            text = f"{k} of {n} selected" if complete else f"{k} selected"
        else:
            text = f"{n} files" if complete else f"{self.model.rowCount()} rows"
        self.item.setProperty("caption", text)
        # a folder listed and found empty says so (not while the listing is still running)
        self.item.setProperty("empty", self.collection is not None and complete and n == 0)

    def dispose(self) -> None:
        if not self.disposed:
            self._watch.stop()
            self.search.cancel()
            self.worker.close()
        super().dispose()


class FileTreeFactory:
    type = "file_tree"
    plugin = FILE_TREE_PLUGIN

    def create(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> FileTreeHost:
        return FileTreeHost(ctx, view_id, manifest)


def factory() -> FileTreeFactory:
    return FileTreeFactory()


def register(registry: Any) -> None:
    registry.add_factory(factory())


__all__ = [
    "FILE_TREE_QML",
    "FileTreeBridge",
    "FileTreeFactory",
    "FileTreeHost",
    "FileTreeModel",
    "Node",
    "ancestors",
    "build_tree",
    "default_expanded",
    "detail_text",
    "factory",
    "register",
    "stamp_ns",
]
