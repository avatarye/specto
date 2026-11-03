"""The file tree's type-to-search (#232, #36, ADR 0022): a query per keystroke through
``specto_intake.search`` on the tree's worker, a flat list of hits while there is text,
and hits that behave like rows.

``TreeSearch`` belongs to one ``FileTreeHost``. ``query(text)`` cancels the query in
flight and runs the new one (Everything on Windows when it can answer for the root,
else a walk); only the latest query's answer lands in :class:`SearchHitsModel`, the
QML's ``searchModel``. Every word of the text matches anywhere in a file's path under
the tree's root (``notes readme`` finds ``docs/notes/readme.md``), name matches first.
Hits pass the tree's lister (its sources, pattern and screener),
so the index is asked for more than the limit. A click picks a hit: ``selected_files``
carries that file (with the stat the search saw) and ``params.selected`` remembers it,
as a pick in the tree does; Ctrl toggles more hits in; a double-click opens its
dedicated view (Ctrl+double-click the contextual preview, #43); ``reveal`` clears the search and loads the folders down to the hit, selected
and scrolled into view. The item's ``searchNote`` says how many hits there are, that
there are more than shown, or, for a miss, which backend ran, so a miss can be
understood (Everything does not see inside archives).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Qt

from specto_core.gestures import OpenedFile
from specto_intake import search as file_search
from specto_intake.search import SearchHit, SearchResult

from .file_icons import icon_for
from .file_tree_format import age_of, format_bytes, format_stamp

if TYPE_CHECKING:
    from .file_tree import FileTreeHost

#: how many hits a query shows at most (the note says when there are more)
LIMIT = 200
CTRL = int(Qt.KeyboardModifier.ControlModifier.value)


class SearchHitsModel(QAbstractListModel):
    """The hits of the latest query, best first."""

    (PathRole, NameRole, FolderRole, IconRole, SelectedRole, SizeRole, StampRole, AgeRole) = range(
        Qt.ItemDataRole.UserRole + 1, Qt.ItemDataRole.UserRole + 9
    )
    ROLES = {
        PathRole: b"path",
        NameRole: b"name",
        FolderRole: b"folder",
        IconRole: b"icon",
        SelectedRole: b"selected",
        SizeRole: b"sizeText",
        StampRole: b"stamp",
        AgeRole: b"age",
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.hits: list[SearchHit] = []
        self.selected: set[str] = set()
        self.now = 0.0

    def set_hits(self, hits: list[SearchHit], now: float) -> None:
        self.beginResetModel()
        self.hits = list(hits)
        self.now = now
        self.endResetModel()

    def set_selected(self, paths: set[str]) -> None:
        self.selected = set(paths)
        if self.hits:
            self.dataChanged.emit(
                self.index(0), self.index(len(self.hits) - 1), [self.SelectedRole]
            )

    def rows(self) -> list[dict[str, Any]]:
        return [
            {
                "path": h.path,
                "name": h.name,
                "folder": h.path.rpartition("/")[0],
                "selected": h.path in self.selected,
            }
            for h in self.hits
        ]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.hits)

    def roleNames(self) -> dict[int, bytes]:  # type: ignore[override]
        return dict(self.ROLES)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self.hits):
            return None
        h = self.hits[index.row()]
        if role in (self.NameRole, Qt.ItemDataRole.DisplayRole):
            return h.name
        if role == self.PathRole:
            return h.path
        if role == self.FolderRole:
            return h.path.rpartition("/")[0]
        if role == self.IconRole:
            return icon_for(h.name, is_dir=False, expanded=False)
        if role == self.SelectedRole:
            return h.path in self.selected
        if role == self.SizeRole:
            return format_bytes(h.size)
        if role == self.StampRole:
            return format_stamp(h.mtime_ns)
        if role == self.AgeRole:
            return age_of(h.mtime_ns, self.now)
        return None


class TreeSearch:
    """One tree's search state (module doc)."""

    def __init__(self, host: FileTreeHost) -> None:
        self.host = host
        self.model = SearchHitsModel(host)
        self.text = ""
        self.result: SearchResult | None = None
        self._gen = 0
        self._token: int | None = None
        #: the Everything backend to ask (None: the module's; tests inject a fake)
        self.everything: file_search.EverythingSearch | None = None

    # -- querying ------------------------------------------------------------------------------
    def query(self, text: str) -> None:
        """A keystroke: the latest text wins; the query in flight is cancelled."""
        self.text = text
        host = self.host
        if self._token is not None:
            host.worker.cancel(self._token)
            self._token = None
        self._gen += 1
        gen = self._gen
        if not text.strip() or not host.root:
            self.result = None
            self.model.set_hits([], host.model.now)
            self._note("")
            self._state("")
            return
        root = host.root
        lister = host.lister
        accept = lister.accepts if lister is not None else None
        everything = self.everything
        self._state("searching")

        def job(cancelled: Any) -> SearchResult:
            return file_search.search(
                root, text, LIMIT, accept=accept, cancelled=cancelled, everything=everything
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
        self.model.set_hits(result.hits, self.host.model.now)
        self.model.set_selected(self.host.model.selected)
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

    # -- hits as rows ------------------------------------------------------------------------
    def hit(self, row: int) -> SearchHit | None:
        return self.model.hits[row] if 0 <= row < len(self.model.hits) else None

    def click(self, row: int, modifiers: int = 0) -> bool:
        """Pick a hit: plain picks it alone, Ctrl toggles it among the picks."""
        h = self.hit(row)
        if h is None:
            return False
        host = self.host
        host._wanted = None
        host._pending = None
        before = set(host.model.selected)
        if modifiers & CTRL:
            host.model.selected ^= {h.path}
        else:
            host.model.selected = {h.path}
        if host.model.selected == before:
            return False
        host.model._changed_rows()
        self.model.set_selected(host.model.selected)
        host._emit_selected(hits={x.path: (x.size, x.mtime_ns) for x in self.model.hits})
        host._remember_selected()
        host._caption()
        return True

    def open(self, row: int, modifiers: int = 0) -> OpenedFile | None:
        """Double-click on a hit (#43): its dedicated view, or with Ctrl the contextual
        preview, as a row's double-click does (``FileTreeHost.open_path``)."""
        h = self.hit(row)
        if h is None:
            return None
        return self.host.open_path(h.path, "contextual" if modifiers & CTRL else "dedicated")

    def reveal(self, row: int) -> bool:
        """Back to the tree at this hit: its folders listed and opened, the row selected
        and scrolled into view."""
        h = self.hit(row)
        if h is None:
            return False
        self.clear()
        self.host.select_paths([h.path])
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
            "hits": [h.path for h in self.model.hits],
            "more": bool(r.more) if r else False,
            "backend": r.backend if r else "",
        }

    def full_path(self, path: str) -> str:
        return path if os.path.isabs(path) else os.path.join(self.host.root, *path.split("/"))


__all__ = ["LIMIT", "SearchHitsModel", "TreeSearch"]
