"""The file tree's type-to-search (#232, #36, SPEC-38, ADR 0022): a query per keystroke
through ``specto_intake.search`` on the tree's worker, and, while there is text, the
tree itself narrowed to the hits.

``TreeSearch`` belongs to one ``FileTreeHost``. ``query(text)`` cancels the query in
flight and runs the new one (Everything on Windows when it can answer for the root,
else a walk); only the latest query's answer lands in :class:`SearchTreeModel`, the
QML's ``searchModel``. Every word of the text matches anywhere in a path under the
tree's root (``notes readme`` finds ``docs/notes/readme.md``), name matches first. Hits
pass the tree's lister (files its sources, pattern and screener; folders
``accepts_dir``), so the index is asked for more than the limit.

The search's tree (SPEC-38) is built from the hits alone, never from a listing of the
project: each hit under the folders above it, every one of those folders open; a
folder that matches by itself shows closed unless hits sit under it, and opening it
lists it on the tree's worker as any folder is. The tree's own rows, open folders and
scroll are left as they were, so clearing the text brings them back. The rows are rows:
the host's gestures (click, Ctrl, Shift, the chevron, double-click, the context menu,
drag) act on whichever tree the list shows (``FileTreeHost.shown``), and the picks are
the tree's own (``SearchTreeModel.selected`` is the tree's set), so a pick made during
a search stays picked after it. A hit file goes out on ``selected_files`` with the stat
the search saw; a picked folder stands for every file under it, resolved on the worker.
``reveal`` clears the search and shows a hit in the tree, its folders listed. The item's
``searchNote`` says how many hits there are, that there are more than shown, or, for a
miss, which backend ran, so a miss can be understood (Everything does not see inside
archives).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject

from specto_core.gestures import OpenedFile
from specto_intake import search as file_search
from specto_intake.listing import Entry
from specto_intake.search import SearchHit, SearchResult

from .file_tree import FileTreeModel, Node, ancestors
from .keys import open_mode_for

if TYPE_CHECKING:
    from .file_tree import FileTreeHost

#: how many hits a query shows at most (the note says when there are more)
LIMIT = 200


class SearchTreeModel(FileTreeModel):
    """The tree while a query is active (SPEC-38): the latest query's hits inside their
    folders. Its picks are the tree's (``tree.selected``); a folder above hits is
    *narrowed* (it holds the hits under it only, and its details line counts them); a
    folder hit with nothing under it is not listed until it is opened."""

    def __init__(self, tree: FileTreeModel, parent: QObject | None = None) -> None:
        self._tree = tree
        picks = tree.selected
        super().__init__(parent)
        tree.selected = picks  # the base's empty set went to the tree: put its picks back
        self.clock = lambda: tree.clock()
        self.now = tree.now
        #: narrowed folder -> how many hits are under it
        self.narrowed: dict[str, int] = {}

    @property  # type: ignore[override]
    def selected(self) -> set[str]:
        return self._tree.selected

    @selected.setter
    def selected(self, paths: set[str]) -> None:
        self._tree.selected = paths

    def set_hits(self, hits: list[SearchHit]) -> None:
        """The hits inside their folders, every folder above a hit open."""
        root = Node("", "", True)
        dirs: dict[str, Node] = {"": root}
        under: dict[str, int] = {}
        known = self._tree

        def folder(path: str) -> Node:
            node = dirs.get(path)
            if node is None:
                parent = folder(path.rpartition("/")[0])
                node = dirs[path] = Node(path, path.rpartition("/")[2], True)
                seen = known.node(path)  # what the tree's listing saw of it, if anything
                if seen is not None and seen.is_dir:
                    node.own_size, node.own_mtime_ns = seen.own_size, seen.own_mtime_ns
                    node.archive = seen.archive
                parent.children.append(node)
            return node

        for h in hits:
            for a in ancestors(h.path)[1:]:
                under[a] = under.get(a, 0) + 1
            if h.is_dir:
                node = folder(h.path)
                node.own_mtime_ns = h.mtime_ns or node.own_mtime_ns
            else:
                parent = folder(h.path.rpartition("/")[0])
                parent.children.append(
                    Node(h.path, h.name, False, size=h.size, mtime_ns=h.mtime_ns)
                )
        for path, node in dirs.items():
            node.children.sort(key=lambda n: (not n.is_dir, n.name.lower()))
            if path and path not in under:
                node.loaded = False  # a folder hit alone: listed when opened
            node.total = None
        self.root = root
        self.narrowed = under
        self._index()
        self.expanded = set(under)
        self.now = self.clock()
        self._set_rows(self._walk())

    def detail_of(self, node: Node) -> str:
        n = self.narrowed.get(node.path)
        if n is None:
            return super().detail_of(node)
        return f"{n} hit{'s' if n != 1 else ''}"

    def stamp_of(self, node: Node) -> int:
        if node.path in self.narrowed:
            return node.own_mtime_ns  # never the newest hit's: the folder holds more
        return super().stamp_of(node)

    def count_of(self, node: Node) -> int:
        n = self.narrowed.get(node.path)
        return n if n is not None else super().count_of(node)

    def _forget(self, node: Node) -> None:
        """A relisted folder's gone rows leave the search's tree; the picks are the
        tree's and stay."""
        for p in [node.path, *(c.path for c in self._descendants(node))]:
            self._nodes.pop(p, None)
            self._parents.pop(p, None)
            self.expanded.discard(p)


class TreeSearch:
    """One tree's search state (module doc)."""

    def __init__(self, host: FileTreeHost) -> None:
        self.host = host
        self.model = SearchTreeModel(host.model, host)
        self.text = ""
        self.result: SearchResult | None = None
        self._gen = 0
        self._token: int | None = None
        #: the Everything backend to ask (None: the module's; tests inject a fake)
        self.everything: file_search.EverythingSearch | None = None

    @property
    def active(self) -> bool:
        """There is text: the list shows the search's tree, not the tree's own."""
        return bool(self.text.strip()) and bool(self.host.root)

    # -- querying ------------------------------------------------------------------------------
    def query(self, text: str) -> None:
        """A keystroke: the latest text wins; the query in flight is cancelled."""
        self.text = text
        host = self.host
        host._anchor = -1  # a Shift range never spans the two trees
        if self._token is not None:
            host.worker.cancel(self._token)
            self._token = None
        self._gen += 1
        gen = self._gen
        if not self.active:
            self.result = None
            self.model.set_hits([])
            self._note("")
            self._state("")
            return
        root = host.root
        lister = host.lister
        accept = lister.accepts if lister is not None else None
        accept_dir = lister.accepts_dir if lister is not None else None
        everything = self.everything
        self._state("searching")

        def job(cancelled: Any) -> SearchResult:
            return file_search.search(
                root,
                text,
                LIMIT,
                accept=accept,
                accept_dir=accept_dir,
                cancelled=cancelled,
                everything=everything,
            )

        token = host.worker.submit(job, lambda result, error: self._done(gen, result, error))
        if host.worker.is_pending(token):
            self._token = token

    def _done(self, gen: int, result: SearchResult | None, error: str | None) -> None:
        if gen != self._gen:
            return  # a later keystroke's query is the one that counts
        self._token = None
        if result is None or result.cancelled:
            if error:
                self._note(f"search failed: {error.splitlines()[0]}")
                self._state("done")
            return
        self.result = result
        self.model.set_hits(result.hits)
        n = len(result.hits)
        if n == 0:
            note = f"no matches · {result.summary()}"
        else:
            note = f"{n} hit{'s' if n != 1 else ''}" + (" · more not shown" if result.more else "")
        self._note(note)
        self._state("done")

    def clear(self) -> None:
        """Back to the tree (Escape, the clear button, new data)."""
        if self.host.item is not None:
            self.host.item.setProperty("searchText", "")
        self.query("")

    def cancel(self) -> None:
        if self._token is not None:
            self.host.worker.cancel(self._token)
            self._token = None

    # -- the search's rows ------------------------------------------------------------------
    def list_folder(self, path: str) -> None:
        """A folder hit opened: list it on the tree's worker into the search's tree (the
        query's answer still showing; a later one drops it)."""
        lister = self.host.lister
        if lister is None:
            return
        gen = self._gen

        def landed(entries: list[Entry] | None, error: str | None) -> None:
            if gen == self._gen and entries is not None:
                self.model.load(path, entries)

        self.host.worker.submit(lambda cancelled: lister.list_dir(path), landed)

    def stats(self) -> dict[str, tuple[int, int]]:
        """``path -> (size, mtime_ns)`` of the file hits: what a pick of one sends."""
        r = self.result
        return {h.path: (h.size, h.mtime_ns) for h in r.hits if not h.is_dir} if r else {}

    def open_first(self, modifiers: int = 0) -> OpenedFile | None:
        """Enter in the search box: the best file hit opens as a double-click would."""
        r = self.result
        best = next((h for h in r.hits if not h.is_dir), None) if r else None
        if best is None:
            return None
        return self.host.open_path(best.path, open_mode_for(modifiers))

    def reveal(self, row: int) -> bool:
        """Back to the tree at this row of the search's tree: the folders above it
        listed and opened, the row selected and scrolled into view."""
        if not 0 <= row < len(self.model._rows):
            return False
        path = self.model._rows[row][0].path
        self.clear()
        self.host.select_paths([path])
        return True

    # -- the item ------------------------------------------------------------------------------
    def _note(self, text: str) -> None:
        if self.host.item is not None:
            self.host.item.setProperty("searchNote", text)

    def _state(self, state: str) -> None:
        if self.host.item is not None:
            self.host.item.setProperty("searchState", state)

    def snapshot(self) -> dict[str, Any]:
        r = self.result
        return {
            "text": self.text,
            "hits": [h.path for h in r.hits] if r else [],
            "more": bool(r.more) if r else False,
            "backend": r.backend if r else "",
        }

    def full_path(self, path: str) -> str:
        return path if os.path.isabs(path) else os.path.join(self.host.root, *path.split("/"))


__all__ = ["LIMIT", "SearchTreeModel", "TreeSearch"]
