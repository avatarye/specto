"""``FileSearch`` (#232): find files by name under a root without loading or expanding
folders. One query is a root, a text and a limit; the answer is the best-matching hits,
whether there were more, and which backend answered. Two backends, chosen per query:

- :class:`EverythingSearch` (Windows): the Everything index over its SDK DLL
  (``specto_intake.everything``), no process per keystroke. Used when the DLL is there,
  the service is running and the root is indexed.
- :class:`ScanSearch` (everywhere else, and the fallback): a cancellable walk from the
  root, one ``scandir`` per folder, checking ``cancelled()`` between folders.

Both match every whitespace-separated word of the text as a case-insensitive substring
of the file name, rank hits the same way (an exact name first, then a name starting with
the text, then the rest; shallower paths and shorter names first within a rank) and give
tree paths (POSIX, relative to the root). Neither looks inside archives, and the index
may miss files its settings exclude: search is for finding, never the source of a
collection (ADR 0022). An ``accept`` predicate (the tree's lister: its sources, pattern
and screener) filters hits the backends know nothing about, so the index is asked for
more than the limit.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .everything import Everything, quote, under

SPECTO_DIR = ".specto"
#: how many results the index is asked for per hit wanted (the screener drops some)
OVERASK = 4
MIN_ASK = 300


@dataclass(frozen=True)
class SearchHit:
    path: str  # tree path: POSIX, relative to the root
    name: str
    size: int = 0
    mtime_ns: int = 0


@dataclass
class SearchResult:
    hits: list[SearchHit] = field(default_factory=list)
    more: bool = False  # there were more matches than ``limit``
    backend: str = "scan"  # "everything" or "scan"
    total: int | None = None  # the matches the index counted (before the filter), if known
    cancelled: bool = False
    elapsed_ms: float = 0.0

    def summary(self) -> str:
        """A line for an empty-result state: which backend ran."""
        where = "Everything" if self.backend == "everything" else "a scan of the folder"
        return f"searched with {where}"


Accept = Callable[[str], bool]
Cancelled = Callable[[], bool]


class FileSearch(Protocol):
    name: str

    def search(
        self,
        root: str,
        text: str,
        limit: int,
        *,
        accept: Accept | None = None,
        cancelled: Cancelled | None = None,
    ) -> SearchResult: ...


def words_of(text: str) -> list[str]:
    return [w.lower() for w in text.split() if w]


def matches(name: str, words: list[str]) -> bool:
    low = name.lower()
    return all(w in low for w in words)


def rank_key(hit: SearchHit, text: str) -> tuple[int, int, int, str]:
    low, want = hit.name.lower(), text.strip().lower()
    if low == want:
        kind = 0
    elif low.startswith(want):
        kind = 1
    else:
        kind = 2
    return (kind, hit.path.count("/"), len(hit.name), hit.path.lower())


def finish(
    hits: list[SearchHit], text: str, limit: int, result: SearchResult, t0: float
) -> SearchResult:
    hits.sort(key=lambda h: rank_key(h, text))
    result.more = result.more or len(hits) > limit
    result.hits = hits[:limit]
    result.elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    return result


def _hidden(tree_path: str) -> bool:
    return tree_path == SPECTO_DIR or tree_path.startswith(SPECTO_DIR + "/")


# -- Everything ---------------------------------------------------------------------------
class EverythingSearch:
    """The Everything index (Windows). ``available(root)`` says whether this backend can
    answer for ``root`` now: the DLL loads, the service has its database, and the root
    has at least one indexed file (a volume Everything does not index has none).
    The per-root answer is remembered for ``probe_ttl_s``."""

    name = "everything"

    def __init__(self, everything: Everything | None = None, *, probe_ttl_s: float = 30.0) -> None:
        self._ev = everything
        self._explicit = everything is not None
        self.probe_ttl_s = probe_ttl_s
        self._indexed: dict[str, tuple[float, bool]] = {}

    @property
    def everything(self) -> Everything | None:
        if self._ev is None and not self._explicit:
            self._ev = Everything.get()
        return self._ev

    def available(self, root: str) -> bool:
        ev = self.everything
        if ev is None or not ev.available():
            return False
        key = os.path.normcase(os.path.abspath(root))
        now = time.monotonic()
        cached = self._indexed.get(key)
        if cached is not None and now - cached[0] < self.probe_ttl_s:
            return cached[1]
        try:
            _hits, total = ev.search(f"{under(root)} file:", limit=1)
            indexed = total > 0
        except OSError:
            indexed = False
        self._indexed[key] = (now, indexed)
        return indexed

    def search(
        self,
        root: str,
        text: str,
        limit: int,
        *,
        accept: Accept | None = None,
        cancelled: Cancelled | None = None,
    ) -> SearchResult:
        t0 = time.perf_counter()
        ev = self.everything
        result = SearchResult(backend=self.name)
        words = words_of(text)
        if ev is None or not words:
            return finish([], text, limit, result, t0)
        base = os.path.normcase(os.path.abspath(root)).replace("/", "\\").rstrip("\\") + "\\"
        query = " ".join([under(root), "file:", *(quote(w) for w in words)])
        ask = max(limit * OVERASK, MIN_ASK)
        raw, total = ev.search(query, limit=ask, cancelled=cancelled)
        result.total = total
        hits: list[SearchHit] = []
        for i, h in enumerate(raw):
            if cancelled is not None and i % 512 == 0 and cancelled():
                result.cancelled = True
                break
            low = os.path.normcase(h.path)
            if not low.startswith(base):
                continue  # a substring match elsewhere: the term is not a prefix filter
            rel = h.path[len(base) :].replace("\\", "/")
            name = rel.rpartition("/")[2]
            if _hidden(rel) or not matches(name, words):
                continue
            if accept is not None and not accept(rel):
                continue
            hits.append(SearchHit(rel, name, h.size, h.mtime_ns))
        result.more = total > len(raw)
        return finish(hits, text, limit, result, t0)


# -- the scan --------------------------------------------------------------------------------
class ScanSearch:
    """A walk from the root, one folder at a time, stopping between folders when
    cancelled. It collects up to ``limit * OVERASK`` matches, so the ranking has
    something to choose from, and says ``more`` when it stopped short."""

    name = "scan"

    def search(
        self,
        root: str,
        text: str,
        limit: int,
        *,
        accept: Accept | None = None,
        cancelled: Cancelled | None = None,
    ) -> SearchResult:
        t0 = time.perf_counter()
        result = SearchResult(backend=self.name)
        words = words_of(text)
        if not words:
            return finish([], text, limit, result, t0)
        cap = max(limit * OVERASK, MIN_ASK)
        hits: list[SearchHit] = []
        stack: list[tuple[str, str]] = [(str(Path(root)), "")]
        while stack:
            if cancelled is not None and cancelled():
                result.cancelled = True
                break
            folder, rel = stack.pop()
            try:
                with os.scandir(folder) as it:
                    entries = list(it)
            except OSError:
                continue
            for e in entries:
                child = f"{rel}/{e.name}" if rel else e.name
                try:
                    if e.is_dir(follow_symlinks=False):
                        if not _hidden(child):
                            stack.append((e.path, child))
                        continue
                    if not matches(e.name, words) or not e.is_file(follow_symlinks=True):
                        continue
                    if accept is not None and not accept(child):
                        continue
                    st = e.stat(follow_symlinks=True)
                except OSError:
                    continue
                hits.append(SearchHit(child, e.name, st.st_size, st.st_mtime_ns))
                if len(hits) >= cap:
                    result.more = True
                    stack.clear()
                    break
        return finish(hits, text, limit, result, t0)


# -- choosing ---------------------------------------------------------------------------------
_everything = EverythingSearch()
_scan = ScanSearch()


def backend_for(root: str, *, everything: EverythingSearch | None = None) -> FileSearch:
    """Everything when it can answer for ``root`` now, else the scan (per query, so a
    service that starts or stops is picked up without a restart). ``SPECTO_SEARCH_BACKEND=scan``
    in the environment forces the scan: the GUI flows run the same on a machine with
    Everything as on one without it."""
    if os.environ.get("SPECTO_SEARCH_BACKEND", "").strip().lower() == "scan":
        return _scan
    ev = everything if everything is not None else _everything
    return ev if ev.available(root) else _scan


def search(
    root: str,
    text: str,
    limit: int = 100,
    *,
    accept: Accept | None = None,
    cancelled: Cancelled | None = None,
    everything: EverythingSearch | None = None,
) -> SearchResult:
    """One query through whichever backend can answer; a failing index (the service
    went away mid-query) falls back to the scan rather than failing the search."""
    backend = backend_for(root, everything=everything)
    try:
        return backend.search(root, text, limit, accept=accept, cancelled=cancelled)
    except OSError:
        if backend is _scan:
            raise
        return _scan.search(root, text, limit, accept=accept, cancelled=cancelled)


__all__ = [
    "MIN_ASK",
    "OVERASK",
    "Accept",
    "Cancelled",
    "EverythingSearch",
    "FileSearch",
    "ScanSearch",
    "SearchHit",
    "SearchResult",
    "backend_for",
    "matches",
    "rank_key",
    "search",
    "words_of",
]
