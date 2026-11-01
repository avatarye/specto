"""Seam 1 (#232): ``FileSearch``. The scan backend finds files by name under a root,
ranks them, respects the tree's filter and the limit, and stops when cancelled; the
backend is chosen per query (the scan when Everything is reported unavailable, or
fails mid-query); and, with Everything running, the index answers a name search under
an indexed folder in under 100 ms (skipped elsewhere)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from intake_helpers import TESTDATA

from specto_intake import search as fs
from specto_intake.listing import TreeLister

#: the indexed folder the latency test searches: the test data by default, a bigger one
#: through SPECTO_SEARCH_ROOT (the index's cost does not grow with the folder)
SEARCH_ROOT = Path(os.environ.get("SPECTO_SEARCH_ROOT") or TESTDATA)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    files = [
        "README.md",
        "notes/readme.txt",
        "notes/deep/er/Readme-draft.md",
        "runs/cam-a/00000.jpg",
        "runs/cam-a/00001.jpg",
        "runs/cam-b/00000.jpg",
        "runs/cam-b/notes.json",
        ".specto/readme.md",
    ]
    for rel in files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * (len(rel) % 7 + 1))
    return tmp_path


def paths(result: fs.SearchResult) -> list[str]:
    return [h.path for h in result.hits]


def test_the_scan_finds_by_name_ranks_exact_then_prefix_then_the_rest_and_skips_specto(
    root: Path,
):
    result = fs.ScanSearch().search(str(root), "readme", 10)
    assert result.backend == "scan" and not result.more and not result.cancelled
    assert paths(result) == ["README.md", "notes/readme.txt", "notes/deep/er/Readme-draft.md"]
    hit = result.hits[1]
    assert (hit.name, hit.size, hit.mtime_ns) == (
        "readme.txt",
        (root / "notes" / "readme.txt").stat().st_size,
        (root / "notes" / "readme.txt").stat().st_mtime_ns,
    )
    # every word must match, anywhere in the name, case-insensitively
    assert paths(fs.ScanSearch().search(str(root), "DRAFT read", 10)) == [
        "notes/deep/er/Readme-draft.md"
    ]
    assert paths(fs.ScanSearch().search(str(root), "   ", 10)) == []
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


class FakeIndex:
    """An Everything the search can be told is up or down, and what it answers."""

    def __init__(self, hits: list[fs.SearchHit] | None = None, *, up: bool = True) -> None:
        self.up = up
        self.hits = hits or []
        self.queries: list[str] = []
        self.fail = False

    def available(self) -> bool:
        return self.up

    def search(self, query: str, *, limit: int, cancelled=None, sort=0):
        self.queries.append(query)
        if self.fail:
            raise OSError("Everything is not running")
        from specto_intake.everything import Hit

        base = query.split('"')[1]
        out = [Hit(base + h.path.replace("/", "\\"), h.size, h.mtime_ns) for h in self.hits]
        return out[:limit], len(out)


def test_everything_answers_when_up_and_indexed_the_scan_when_not(root: Path):
    index = FakeIndex([fs.SearchHit("runs/cam-a/00001.jpg", "00001.jpg", 5, 7)])
    everything = fs.EverythingSearch(index, probe_ttl_s=0)  # type: ignore[arg-type]
    assert fs.backend_for(str(root), everything=everything) is everything
    result = fs.search(str(root), "0000", 10, everything=everything)
    assert result.backend == "everything" and paths(result) == ["runs/cam-a/00001.jpg"]
    assert result.hits[0].size == 5 and result.total == 1
    # the index is asked for more than the limit, for files only, word by word, under the root
    assert index.queries[-1].endswith('\\" file: "0000"')
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
    everything = fs.EverythingSearch(index, probe_ttl_s=0)  # type: ignore[arg-type]
    lister = TreeLister(root, {"sources": ["."], "screener": ["@text"]}, {"@text": [".txt"]})
    result = fs.search(str(root), "notes", 10, accept=lister.accepts, everything=everything)
    assert paths(result) == []  # notes.json is not text, .specto/ is hidden, readme has no "notes"
    result = fs.search(str(root), "readme", 10, accept=lister.accepts, everything=everything)
    assert paths(result) == ["notes/readme.txt"]


def test_the_environment_can_force_the_scan(root: Path, monkeypatch: pytest.MonkeyPatch):
    """``SPECTO_SEARCH_BACKEND=scan``: the walk answers even when an index could (the
    GUI flows run the same on a machine with Everything as on one without it)."""
    index = FakeIndex([fs.SearchHit("README.md", "README.md")])
    everything = fs.EverythingSearch(index, probe_ttl_s=0)  # type: ignore[arg-type]
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
