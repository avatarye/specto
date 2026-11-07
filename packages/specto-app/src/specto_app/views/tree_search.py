"""The file tree's type-to-search (#232, #36, SPEC-38, ADR 0022): a query per keystroke
through ``specto_intake.search`` on the tree's worker, and, while there is text, the
search tree in the tree's list.

``TreeSearch`` belongs to one ``FileTreeHost``. ``query(text)`` cancels the query in
flight and runs the new one (Everything on Windows when it can answer for the root,
else a walk); only the latest query's answer lands in the search tree
(``file_tree.SearchTreeModel``, the QML's ``searchModel``). Every word of the text
matches anywhere in a file's path under the tree's root (``notes readme`` finds
``docs/notes/readme.md``), name matches first; a folder is a hit when its own name holds
every word. Hits pass the tree's lister (``hit_filter()``), so the index is asked for
more than the limit.

The search tree (SPEC-38) is built from the hits alone, never from a listing of the
project: each hit under the folders above it, every one of those folders open. A
folder hit is a real folder: listed in full on the tree's worker when hits sit under
it, closed and listed on open when none do. The tree's own rows, open folders and
scroll are left as they were, so clearing the text brings them back. The rows are rows:
the host's gestures (click, Ctrl, Shift, the chevron, double-click, the context menu,
drag, the keys) act on whichever tree the list shows (``FileTreeHost.shown``), and the
picks are one set (``file_tree.Picks``), so a pick made during a search stays picked
after it; picks set from outside meanwhile are shown in the tree once it clears
(``FileTreeHost.search_cleared``). A hit file goes out on ``selected_files`` with the
stat the search saw; a picked folder stands for every file under it, resolved on the
worker. ``reveal`` clears the search and shows a row in the tree, its folders listed.
The item's ``searchNote`` says how many hits there are, that there are more than shown,
or, for a miss, which backend ran, so a miss can be understood (Everything does not see
inside archives).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from specto_core.gestures import OpenedFile
from specto_intake import search as file_search
from specto_intake.listing import Entry
from specto_intake.search import SearchResult

from .keys import open_mode_for

if TYPE_CHECKING:
    from .file_tree import FileTreeHost, SearchTreeModel

#: how many hits a query shows at most (the note says when there are more)
LIMIT = 200


class TreeSearch:
    """One tree's search state (module doc)."""

    def __init__(self, host: FileTreeHost, model: SearchTreeModel) -> None:
        self.host = host
        self.model = model
        #: search-tree folders whose listing is in flight
        self._loading: set[str] = set()
        self.text = ""
        self.result: SearchResult | None = None
        self._gen = 0
        self._token: int | None = None
        #: the search tree's folder listings submitted for this query: a new query
        #: cancels them, so the next search does not wait behind them (SPEC-40)
        self._listings: set[int] = set()
        #: the Everything backend to ask (None: the module's; tests inject a fake)
        self.everything: file_search.EverythingSearch | None = None

    @property
    def active(self) -> bool:
        """There is text: the list shows the search tree, not the tree's own."""
        return bool(self.text.strip()) and bool(self.host.root)

    # -- querying ------------------------------------------------------------------------------
    def query(self, text: str) -> None:
        """A keystroke: the latest text wins; the query in flight is cancelled."""
        was_active = self.active
        self.text = text
        host = self.host
        self._loading.clear()  # a listing still in flight lands nowhere (its gen is old)
        host._anchor = -1  # a Shift range never spans the two trees
        self.cancel()
        self._gen += 1
        gen = self._gen
        if not self.active:
            self.result = None
            self.model.set_hits([])
            self._note("")
            self._state("")
            if was_active:
                host.search_cleared()
            return
        root = host.root
        lister = host.lister
        hit_filter = lister.hit_filter() if lister is not None else None
        everything = self.everything
        self._state("searching")

        def job(cancelled: Any) -> SearchResult:
            return file_search.search(
                root,
                text,
                LIMIT,
                hit_filter=hit_filter,
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
        for path in self.model.to_list:  # folder hits with hits under them: in full
            self.list_folder(path)
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
        """Cancel the query in flight and the search tree's folder listings."""
        if self._token is not None:
            self.host.worker.cancel(self._token)
            self._token = None
        for t in self._listings:
            self.host.worker.cancel(t)
        self._listings.clear()

    # -- the search tree's rows ------------------------------------------------------------------
    def list_folder(self, path: str) -> None:
        """List a folder of the search tree on the tree's worker, once (a folder hit, or
        a folder of one opened); the answer lands while the same query's tree shows."""
        lister = self.host.lister
        if lister is None or path in self._loading:
            return
        self._loading.add(path)
        gen = self._gen
        token: list[int] = []  # set once submitted (inline jobs land inside submit)

        def landed(entries: list[Entry] | None, error: str | None) -> None:
            if token:
                self._listings.discard(token[0])
            if gen != self._gen:
                return
            self._loading.discard(path)
            if error is not None:
                self.host.set_health("error", error.splitlines()[0])
                return
            if entries is not None:
                self.model.load(path, entries)

        t = self.host.worker.submit(lambda cancelled: lister.list_dir(path, cancelled), landed)
        if self.host.worker.is_pending(t):
            token.append(t)
            self._listings.add(t)

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
        """Back to the tree at this row of the search tree: the folders above it
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


__all__ = ["LIMIT", "TreeSearch"]
