"""Seam 1 (#232, #36): ``FileSearch``. Both backends find files by their path under a
root (every word anywhere in the relative path, never in the folders above the root),
rank them (name matches above path-only ones), respect the tree's filter and the limit,
and stop when cancelled; the index is asked only for the words that narrow it and read
through up to a bound, past which the scan answers; the backend is chosen per query
(the scan when Everything is reported unavailable, or fails mid-query); and, with
Everything running, the index agrees with the scan on the test data and answers a
search under an indexed folder in under 100 ms (skipped elsewhere)."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest
from intake_helpers import TESTDATA

from specto_intake import search as fs
from specto_intake.everything import Hit
from specto_intake.listing import TreeLister

#: the indexed folder the latency test searches: the test data by default, a bigger one
#: through SPECTO_SEARCH_ROOT (the index's cost does not grow with the folder)
SEARCH_ROOT = Path(os.environ.get("SPECTO_SEARCH_ROOT") or TESTDATA)
FILES = [
    "README.md",
    "notes/readme.txt",
    "notes/deep/er/Readme-draft.md",
    "runs/cam-a/00000.jpg",
    "runs/cam-a/00001.jpg",
    "runs/cam-b/00000.jpg",
    "runs/cam-b/notes.json",
    ".specto/readme.md",
]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """The fixture files under ``zebra/proj``: ``zebra`` is a word above the root."""
    root = tmp_path / "zebra" / "proj"
    for rel in FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * (len(rel) % 7 + 1))
    return root


def paths(result: fs.SearchResult) -> list[str]:
    return [h.path for h in result.hits]


class FakeIndex:
    """An Everything the search can be told is up or down, and what it holds: every
    ``path:"..."`` term of a query is matched against the whole path and every
    ``regex:"..."`` term searched in it case-insensitively (as Everything does), the
    root term restricts, pages come from ``offset``."""

    def __init__(self, hits: list[fs.SearchHit] | None = None, *, up: bool = True) -> None:
        self.up = up
        self.hits = hits or []
        self.queries: list[str] = []
        self.pages: list[tuple[int, int]] = []  # (offset, limit) per query
        self.fail = False

    def available(self) -> bool:
        return self.up

    def search(self, query: str, *, limit: int, offset: int = 0, cancelled=None, sort=0, keep=None):
        self.queries.append(query)
        self.pages.append((offset, limit))
        if self.fail:
            raise OSError("Everything is not running")
        base = query.split('"')[1]
        terms = [t.lower() for t in re.findall(r'path:"([^"]*)"', query)]
        regexes = re.findall(r'regex:"([^"]*)"', query)
        # files, and without ``file:`` the folders above them too (as the index holds them)
        entries: dict[str, Hit] = {}
        for h in self.hits:
            if " file:" not in query:
                parts = h.path.split("/")
                for i in range(1, len(parts)):
                    folder = base + "\\".join(parts[:i])
                    entries.setdefault(folder, Hit(folder, 0, 9, is_dir=True))
            full = base + h.path.replace("/", "\\")
            entries[full] = Hit(full, h.size, h.mtime_ns)
        out = [
            h
            for full, h in sorted(entries.items())
            if all(t in full.lower() for t in terms)
            and all(re.search(r, full, re.IGNORECASE) for r in regexes)
        ]
        page = out[offset : offset + limit]
        return [h for h in page if keep is None or keep(h.path, h.is_dir)], len(out)


def fake_everything(index: FakeIndex) -> fs.EverythingSearch:
    return fs.EverythingSearch(index, probe_ttl_s=0)  # type: ignore[arg-type]


def backends() -> list[tuple[str, fs.FileSearch]]:
    index = FakeIndex([fs.SearchHit(p, p.rpartition("/")[2]) for p in FILES])
    return [("scan", fs.ScanSearch()), ("everything", fake_everything(index))]


CASES = [
    # exact name, then a name starting with the text, then the rest; .specto/ is hidden
    ("readme", ["README.md", "notes/readme.txt", "notes/deep/er/Readme-draft.md"]),
    # every word must match, anywhere in the path, case-insensitively
    ("DRAFT read", ["notes/deep/er/Readme-draft.md"]),
    ("   ", []),
    # a folder's name finds the files under it (#36)
    ("deep", ["notes/deep/er/Readme-draft.md"]),
    ("cam-a", ["runs/cam-a/00000.jpg", "runs/cam-a/00001.jpg"]),
    # two words, one in a folder and one in the name
    ("er draft", ["notes/deep/er/Readme-draft.md"]),
    ("cam-b 0000", ["runs/cam-b/00000.jpg"]),
    # a word with a slash (either kind) matches across the separator
    ("notes/read", ["notes/readme.txt"]),
    ("notes\\read", ["notes/readme.txt"]),
    ("NOTES/DEEP/ER", ["notes/deep/er/Readme-draft.md"]),
    # a word that appears only above the root matches nothing, alone or with others
    ("zebra", []),
    ("zebra readme", []),
    ("proj", []),
    # name matches rank above path-only matches; shallower first within a rank
    (
        "notes",
        ["runs/cam-b/notes.json", "notes/readme.txt", "notes/deep/er/Readme-draft.md"],
    ),
    # a letter in every name that holds it, shallower first; never the jpgs
    (
        "e",
        ["README.md", "notes/readme.txt", "runs/cam-b/notes.json", "notes/deep/er/Readme-draft.md"],
    ),
]


@pytest.mark.parametrize(("name", "backend"), backends(), ids=["scan", "everything"])
@pytest.mark.parametrize(("text", "expected"), CASES, ids=[c[0] for c in CASES])
def test_both_backends_match_the_relative_path_and_rank_names_first(
    root: Path, name: str, backend: fs.FileSearch, text: str, expected: list[str]
):
    result = backend.search(str(root), text, 10)
    assert result.backend == name and not result.more and not result.cancelled
    assert paths(result) == expected
    assert all(h.name == h.path.rpartition("/")[2] for h in result.hits)


FOLDER_CASES = [
    # a folder named after the word ranks first; the folders and files below it follow
    ("deep", ["notes/deep", "notes/deep/er", "notes/deep/er/Readme-draft.md"]),
    (
        "cam",
        [
            "runs/cam-a",
            "runs/cam-b",
            "runs/cam-a/00000.jpg",
            "runs/cam-a/00001.jpg",
            "runs/cam-b/00000.jpg",
            "runs/cam-b/notes.json",
        ],
    ),
    # .specto/ stays hidden, as a folder too
    ("specto", []),
]


@pytest.mark.parametrize(("name", "backend"), backends(), ids=["scan", "everything"])
@pytest.mark.parametrize(("text", "expected"), FOLDER_CASES, ids=[c[0] for c in FOLDER_CASES])
def test_both_backends_find_folders_when_asked_for_them(
    root: Path, name: str, backend: fs.FileSearch, text: str, expected: list[str]
):
    """SPEC-38: with ``accept_dir`` a folder whose path holds every word is a hit too,
    ranked like a file; it passes ``accept_dir``, never the files' ``accept``."""
    result = backend.search(str(root), text, 10, accept=lambda p: True, accept_dir=lambda p: True)
    assert result.backend == name
    assert paths(result) == expected
    assert {h.path for h in result.hits if h.is_dir} == {p for p in expected if "." not in p}
    # the tree's lister: folders under its sources, not hidden
    lister = TreeLister(root, {"sources": ["runs"]})
    result = backend.search(
        str(root), "notes", 10, accept=lister.accepts, accept_dir=lister.accepts_dir
    )
    assert paths(result) == ["runs/cam-b/notes.json"]  # not the notes/ folder: outside


def test_the_scan_reports_the_files_stat_and_names_itself(root: Path):
    result = fs.ScanSearch().search(str(root), "readme", 10)
    hit = result.hits[1]
    assert (hit.name, hit.size, hit.mtime_ns) == (
        "readme.txt",
        (root / "notes" / "readme.txt").stat().st_size,
        (root / "notes" / "readme.txt").stat().st_mtime_ns,
    )
    assert result.summary() == "searched with a scan of the folder"


def test_hits_pass_the_trees_filter_and_the_limit_says_when_there_are_more(root: Path):
    lister = TreeLister(root, {"sources": ["runs"], "screener": ["*.jpg"]})
    result = fs.ScanSearch().search(str(root), "0000", 10, accept=lister.accepts)
    assert paths(result) == [  # not notes.json; 00001 holds "0000" too
        "runs/cam-a/00000.jpg",
        "runs/cam-a/00001.jpg",
        "runs/cam-b/00000.jpg",
    ]
    result = fs.ScanSearch().search(str(root), "0", 2)
    assert len(result.hits) == 2 and result.more


def test_the_scan_stops_between_folders_when_cancelled(root: Path):
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    result = fs.ScanSearch().search(str(root), "0", 10, cancelled=cancelled)
    assert result.cancelled and len(result.hits) < 4


def test_everything_answers_when_up_and_indexed_the_scan_when_not(root: Path):
    index = FakeIndex([fs.SearchHit("runs/cam-a/00001.jpg", "00001.jpg", 5, 7)])
    everything = fake_everything(index)
    assert fs.backend_for(str(root), everything=everything) is everything
    result = fs.search(str(root), "0000", 10, everything=everything)
    assert result.backend == "everything" and paths(result) == ["runs/cam-a/00001.jpg"]
    assert result.hits[0].size == 5 and result.total == 1
    # the index is asked for more than the limit, for files only, word by word as path
    # terms, under the root
    assert index.queries[-1].endswith('\\" file: path:"0000"')
    assert index.pages[-1] == (0, fs.MIN_ASK)
    assert result.summary() == "searched with Everything"

    index.up = False  # the service stopped: the scan answers, nothing fails
    assert fs.backend_for(str(root), everything=everything).name == "scan"
    assert fs.search(str(root), "0000", 10, everything=everything).backend == "scan"

    index.up, index.hits = True, []  # up, but the root is not indexed: the scan
    assert fs.backend_for(str(root), everything=everything).name == "scan"

    index.hits = [fs.SearchHit("README.md", "README.md")]
    everything._indexed.clear()
    assert fs.backend_for(str(root), everything=everything) is everything
    index.fail = True  # it went away mid-query: the scan takes over for this query
    result = fs.search(str(root), "readme", 10, everything=everything)
    assert result.backend == "scan" and "README.md" in paths(result)


def test_everything_hits_are_filtered_like_the_scans(root: Path):
    index = FakeIndex(
        [
            fs.SearchHit("runs/cam-b/notes.json", "notes.json"),
            fs.SearchHit("notes/readme.txt", "readme.txt"),
            fs.SearchHit(".specto/notes.md", "notes.md"),
        ]
    )
    everything = fake_everything(index)
    lister = TreeLister(root, {"sources": ["."], "screener": ["@text"]}, {"@text": [".txt"]})
    result = fs.search(str(root), "cam-b", 10, accept=lister.accepts, everything=everything)
    assert paths(result) == []  # notes.json is not text, .specto/ is hidden, readme is elsewhere
    result = fs.search(str(root), "readme", 10, accept=lister.accepts, everything=everything)
    assert paths(result) == ["notes/readme.txt"]
    result = fs.search(str(root), "notes", 10, accept=lister.accepts, everything=everything)
    assert paths(result) == ["notes/readme.txt"]  # through its folder; notes.json is not text


def test_words_in_the_roots_own_path_go_to_the_index_as_regexes_anchored_below_it(
    root: Path,
):
    """``zebra`` is above the root: as a ``path:`` term it would match every file under
    the root, so it goes as a regex anchored at the root's path, which finds it only
    below; the result is checked against the relative path regardless."""
    index = FakeIndex([fs.SearchHit(p, p.rpartition("/")[2]) for p in FILES])
    everything = fake_everything(index)
    terms = fs.index_terms(str(root), ["zebra", "readme", "zebra/proj", "deep/er"])
    assert terms[1] == 'path:"readme"' and terms[3] == 'path:"deep\\er"'
    native = re.escape(str(root) + "\\")
    assert (
        terms[0] == f'regex:"^{native}.*zebra"' and terms[2] == f'regex:"^{native}.*zebra\\\\proj"'
    )
    result = fs.search(str(root), "zebra readme", 10, everything=everything)
    assert result.backend == "everything" and paths(result) == [] and result.total == 0
    assert index.queries[-1].endswith(f'\\" file: regex:"^{native}.*zebra" path:"readme"')
    result = fs.search(str(root), "zebra", 10, everything=everything)
    assert paths(result) == [] and result.total == 0 and not result.more
    # a file with the root's word below the root is found through the regex
    index.hits.append(fs.SearchHit("runs/zebra.csv", "zebra.csv"))
    result = fs.search(str(root), "zebra", 10, everything=everything)
    assert paths(result) == ["runs/zebra.csv"] and result.total == 1
    assert paths(fs.ScanSearch().search(str(root), "zebra", 10)) == []  # not on disk


def test_the_index_is_read_in_two_pages_up_to_the_bound_then_the_scan_answers(
    root: Path, monkeypatch: pytest.MonkeyPatch
):
    """A broad word under a big root: the first page is the usual ask; when the hits do
    not fill the limit and candidates remain, the rest come in one more page, up to the
    bound. Past the bound with the limit unfilled, the exhaustive scan answers instead
    of an unfinished list; with it filled, ``more`` is true and said."""
    many = [f"a/file-{i:05d}.txt" for i in range(2000)] + [f"b/needle-{i}.txt" for i in range(5)]
    index = FakeIndex([fs.SearchHit(p, p.rpartition("/")[2]) for p in many])
    calls: list[str] = []

    class Fallback:
        name = "scan"

        def search(self, root, text, limit, *, accept=None, cancelled=None, accept_dir=None):
            calls.append(text)
            return fs.SearchResult(backend="scan", hits=[fs.SearchHit("b/needle-0.txt", "x")])

    everything = fs.EverythingSearch(index, probe_ttl_s=0, fallback=Fallback())  # type: ignore[arg-type]
    text_only = lambda rel: "needle" in rel  # noqa: E731  (the tree's screener)

    # within the bound: two pages, the second fetching every remaining candidate
    monkeypatch.setattr(fs, "INDEX_BOUND", 5000)
    result = everything.search(str(root), "txt", 10, accept=text_only)
    assert paths(result) == [f"b/needle-{i}.txt" for i in range(5)]
    assert not result.more and result.total == 2005 and calls == []
    assert index.pages[-2:] == [(0, fs.MIN_ASK), (fs.MIN_ASK, 2005 - fs.MIN_ASK)]

    # past the bound with the limit unfilled: the scan answers
    monkeypatch.setattr(fs, "INDEX_BOUND", 1000)
    result = everything.search(str(root), "txt", 10, accept=text_only)
    assert result.backend == "scan" and calls == ["txt"] and paths(result) == ["b/needle-0.txt"]
    assert index.pages[-2:] == [(0, fs.MIN_ASK), (fs.MIN_ASK, 1000 - fs.MIN_ASK)]

    # the limit filled from the first page: one page, and ``more`` is true
    before = len(index.pages)
    result = everything.search(str(root), "file", 10)
    assert len(result.hits) == 10 and result.more and result.total == 2000
    assert index.pages[before:] == [(0, fs.MIN_ASK)] and calls == ["txt"]

    # a cancellation mid-page stops the read
    calls.clear()
    monkeypatch.setattr(fs, "INDEX_BOUND", 5000)
    result = everything.search(str(root), "txt", 10, accept=text_only, cancelled=lambda: True)
    assert result.cancelled and calls == []


def test_the_environment_can_force_the_scan(root: Path, monkeypatch: pytest.MonkeyPatch):
    """``SPECTO_SEARCH_BACKEND=scan``: the walk answers even when an index could (the
    GUI flows run the same on a machine with Everything as on one without it)."""
    index = FakeIndex([fs.SearchHit("README.md", "README.md")])
    everything = fake_everything(index)
    monkeypatch.setenv("SPECTO_SEARCH_BACKEND", "scan")
    assert fs.backend_for(str(root), everything=everything).name == "scan"
    result = fs.search(str(root), "readme", 10, everything=everything)
    assert result.backend == "scan" and "notes/readme.txt" in paths(result)
    assert index.queries == []
    monkeypatch.setenv("SPECTO_SEARCH_BACKEND", "")
    assert fs.backend_for(str(root), everything=everything) is everything


def test_a_name_search_under_an_indexed_folder_answers_in_under_100_ms_through_the_dll():
    everything = fs.EverythingSearch()
    if not SEARCH_ROOT.exists() or not everything.available(str(SEARCH_ROOT)):
        pytest.skip("Everything is not running here, or the folder is not indexed")
    everything.available(str(SEARCH_ROOT))  # the probe, cached
    t0 = time.perf_counter()
    result = fs.search(str(SEARCH_ROOT), "readme", 100, everything=everything)
    elapsed = time.perf_counter() - t0
    assert result.backend == "everything" and result.hits
    assert all(h.path.lower().count("readme") for h in result.hits)
    assert all(not os.path.isabs(h.path) and "\\" not in h.path for h in result.hits)
    assert elapsed < 0.1, f"{elapsed * 1000:.1f} ms (Everything reported {result.elapsed_ms} ms)"


@pytest.mark.parametrize(
    "text",
    [
        "notes",  # a folder's name, and an archive named after it
        "cam-a 0001",  # a folder and a name
        "video/cam",  # across the separator
        "001\\meta",
        "GEO JSON",
        "testdata",  # the root's own name: in no path below it
        "runs readme",  # a folder and a word only in the root's README
        "e",  # in the root's path, and below it: the anchored regex
        "es notes",
    ],
)
def test_everything_and_the_scan_agree_on_the_test_data(text: str):
    """The same hits in the same order from both backends, on a folder the index holds
    (skipped without Everything). ``testdata``, ``e`` and ``es`` are in the root's own
    path: the index is asked for them as regexes anchored below the root."""
    everything = fs.EverythingSearch()
    if not TESTDATA.exists() or not everything.available(str(TESTDATA)):
        pytest.skip("Everything is not running here, or the test data is not indexed")
    scan = fs.ScanSearch().search(str(TESTDATA), text, 100)
    index = everything.search(str(TESTDATA), text, 100)
    assert index.backend == "everything" and not index.more and not scan.more
    assert paths(index) == paths(scan)
    assert bool(scan.hits) == (text not in ("testdata", "runs readme"))
    # asked for folders too (SPEC-38): the same folders, flagged as folders
    scan = fs.ScanSearch().search(str(TESTDATA), text, 100, accept_dir=lambda p: True)
    index = everything.search(str(TESTDATA), text, 100, accept_dir=lambda p: True)
    assert [(h.path, h.is_dir) for h in index.hits] == [(h.path, h.is_dir) for h in scan.hits]
    if text == "notes":  # the notes/ folder
        assert any(h.is_dir for h in index.hits)
