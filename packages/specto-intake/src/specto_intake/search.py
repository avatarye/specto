"""``FileSearch`` (#232, #36): find files by path under a root without loading or
expanding folders. One query is a root, a text and a limit; the answer is the
best-matching hits, whether there were more, and which backend answered. Two backends,
chosen per query:

- :class:`EverythingSearch` (Windows): the Everything index over its SDK DLL
  (``specto_intake.everything``), no process per keystroke. Used when the DLL is there,
  the service is running and the root is indexed.
- :class:`ScanSearch` (everywhere else, and the fallback): a cancellable walk from the
  root, one ``scandir`` per folder, checking ``cancelled()`` between folders.

Both match every whitespace-separated word of the text as a case-insensitive substring
of the file's tree path (POSIX, relative to the root: ``notes readme`` finds
``docs/notes/readme.md``, and a word with a slash, ``docs/read`` or ``docs\\read``,
matches across the separator). The folders above the root never count. Both rank hits
the same way: an exact name first, then a name starting with the text, then names
holding every word, then the hits some word reaches only through the folders; shallower
paths and shorter names first within a rank. Neither looks inside archives, and the
index may miss files its settings exclude: search is for finding, never the source of a
collection (ADR 0022). An ``accept`` predicate (the tree's lister: its sources, pattern
and screener) filters hits the backends know nothing about, so the index is asked for
more than the limit.

Folders are hits only when the caller asks for them with ``accept_dir`` (SPEC-38: the
tree shows a folder that itself matches): a folder whose path below the root holds
every word, ranked as a file is, passing ``accept_dir`` (the lister's
``accepts_dir``: under a source, not hidden) rather than ``accept``. Everything then runs
the query without its ``file:`` filter and reads each result's folder flag; the scan
checks the folders it walks anyway.

Everything matches a ``path:`` term against the absolute path, so a word that occurs in
the root's own path (``specto`` under ``C:/work/specto``) would match every file under
it. Such a word goes to the index as a regular expression anchored at the root instead
(``below_term``), so its candidates are the files with the word below the root, at the
cost of a regex pass over the files under the root (about 25 ms for 15k files). Every
candidate is checked against the relative path here regardless. When the candidates run
past what the first page holds and the hits do not fill the limit (the screener drops
most of a broad word's candidates), the rest are fetched in one more page up to
``INDEX_BOUND``; past that bound an unfinished answer is never shown as finished: the
scan answers the query instead (exhaustive, cancellable), so ``more`` is only ever said
when it is true.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .everything import Everything, below_term, path_term, under

SPECTO_DIR = ".specto"
#: how many results the index is asked for per hit wanted (the screener drops some)
OVERASK = 4
MIN_ASK = 300
#: how many candidates the index is read through at most for one query (about 65 ms
#: for 50k paths through the DLL); past it the scan answers instead
INDEX_BOUND = 50_000


@dataclass(frozen=True)
class SearchHit:
    path: str  # tree path: POSIX, relative to the root
    name: str
    size: int = 0
    mtime_ns: int = 0
    is_dir: bool = False  # a folder (only when the search was asked for folders)


@dataclass
class SearchResult:
    hits: list[SearchHit] = field(default_factory=list)
    more: bool = False  # there were more matches than ``limit``
    backend: str = "scan"  # "everything" or "scan"
    total: int | None = None  # the candidates the index counted (before the filter), if known
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
        accept_dir: Accept | None = None,
    ) -> SearchResult: ...


def words_of(text: str) -> list[str]:
    """The query's words, lowercased, with backslashes read as slashes."""
    return [w.lower().replace("\\", "/") for w in text.split() if w]


def matches(path: str, words: list[str]) -> bool:
    """Does every word occur in ``path`` (a tree path, or a name), case-insensitively?"""
    low = path.lower()
    return all(w in low for w in words)


def rank_key(hit: SearchHit, text: str) -> tuple[int, int, int, str]:
    low, want = hit.name.lower(), text.strip().lower()
    if low == want:
        kind = 0
    elif low.startswith(want):
        kind = 1
    elif matches(low, words_of(text)):
        kind = 2  # every word in the name
    else:
        kind = 3  # some word only in the folders above it
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


def index_terms(root: str, words: list[str]) -> list[str]:
    """The index's term for each word: a ``path:`` term, or, for a word that occurs in
    the root's own path (where that term would match every file under the root), a
    regular expression anchored below the root."""
    root_low = os.path.normcase(os.path.abspath(root)).replace("\\", "/").lower() + "/"
    return [below_term(root, w) if w in root_low else path_term(w) for w in words]


# -- Everything ---------------------------------------------------------------------------
class EverythingSearch:
    """The Everything index (Windows). ``available(root)`` says whether this backend can
    answer for ``root`` now: the DLL loads, the service has its database, and the root
    has at least one indexed file (a volume Everything does not index has none).
    The per-root answer is remembered for ``probe_ttl_s``. ``fallback`` answers a
    query whose candidates run past ``INDEX_BOUND`` before the hits fill the limit
    (module doc)."""

    name = "everything"

    def __init__(
        self,
        everything: Everything | None = None,
        *,
        probe_ttl_s: float = 30.0,
        fallback: FileSearch | None = None,
    ) -> None:
        self._ev = everything
        self._explicit = everything is not None
        self.probe_ttl_s = probe_ttl_s
        self.fallback: FileSearch = fallback if fallback is not None else ScanSearch()
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
        accept_dir: Accept | None = None,
    ) -> SearchResult:
        t0 = time.perf_counter()
        ev = self.everything
        result = SearchResult(backend=self.name)
        words = words_of(text)
        if ev is None or not words:
            return finish([], text, limit, result, t0)
        base = os.path.normcase(os.path.abspath(root)).replace("/", "\\").rstrip("\\") + "\\"
        blen = len(base)
        kinds = [] if accept_dir is not None else ["file:"]
        query = " ".join([under(root), *kinds, *index_terms(root, words)])

        def keep(path: str, is_dir: bool) -> bool:
            if path[:blen].lower() != base:
                return False  # a substring match elsewhere: the term is not a prefix filter
            rel = path[blen:].replace("\\", "/")
            if not rel or _hidden(rel) or not matches(rel, words):
                return False
            if is_dir:
                return accept_dir is not None and accept_dir(rel)
            return accept is None or accept(rel)

        cap = max(limit * OVERASK, MIN_ASK)
        hits: list[SearchHit] = []
        seen = 0  # candidates read so far
        total = 0
        done = False
        while True:
            ask = cap if seen == 0 else min(total, INDEX_BOUND) - seen
            raw, total = ev.search(query, limit=ask, offset=seen, cancelled=cancelled, keep=keep)
            result.total = total
            if cancelled is not None and cancelled():
                result.cancelled = True
                return finish(hits, text, limit, result, t0)
            for h in raw:
                rel = h.path[blen:].replace("\\", "/")
                size = 0 if h.is_dir else h.size
                hits.append(SearchHit(rel, rel.rpartition("/")[2], size, h.mtime_ns, h.is_dir))
            seen = min(seen + ask, total)
            if seen >= total:
                done = True  # every candidate was read: the answer is exact
                break
            if len(hits) > limit:
                break  # the limit is full and candidates remain: ``more`` is true
            if seen >= INDEX_BOUND:
                break
        if not done and len(hits) <= limit:
            # the index could not be read through within the bound and the hits do not
            # fill the limit: the exhaustive scan answers rather than an unfinished list
            return self.fallback.search(
                root, text, limit, accept=accept, cancelled=cancelled, accept_dir=accept_dir
            )
        result.more = not done
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
        accept_dir: Accept | None = None,
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
                        if _hidden(child):
                            continue
                        stack.append((e.path, child))
                        if accept_dir is None or not matches(child, words):
                            continue
                        if not accept_dir(child):
                            continue
                        mtime_ns = e.stat(follow_symlinks=False).st_mtime_ns
                        hits.append(SearchHit(child, e.name, 0, mtime_ns, True))
                        if len(hits) >= cap:
                            result.more = True
                            stack.clear()
                            break
                        continue
                    if not matches(child, words) or not e.is_file(follow_symlinks=True):
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
_scan = ScanSearch()
_everything = EverythingSearch(fallback=_scan)


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
    accept_dir: Accept | None = None,
) -> SearchResult:
    """One query through whichever backend can answer; a failing index (the service
    went away mid-query) falls back to the scan rather than failing the search."""
    backend = backend_for(root, everything=everything)
    try:
        return backend.search(
            root, text, limit, accept=accept, cancelled=cancelled, accept_dir=accept_dir
        )
    except OSError:
        if backend is _scan:
            raise
        return _scan.search(
            root, text, limit, accept=accept, cancelled=cancelled, accept_dir=accept_dir
        )


__all__ = [
    "INDEX_BOUND",
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
    "index_terms",
    "matches",
    "rank_key",
    "search",
    "words_of",
]
