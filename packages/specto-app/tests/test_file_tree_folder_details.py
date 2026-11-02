"""A folder row's details line (#38): never empty. A folder shows its own modified time
(the stat its parent's listing saw, coloured by age like a file) and, once it is listed,
how many items the listing shows (``12 items``, ``empty``); an archive its own file's
size; a folder nothing is known about yet says so. The model alone, over the real
lister on a temporary folder: no window."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from specto_app.views.file_tree import FileTreeModel
from specto_app.views.file_tree_format import format_bytes, format_stamp
from specto_intake.listing import TreeLister

NOW = 1_800_000_000.0  # the fake clock the ages are judged against


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """``notes/`` with three files, ``empty/``, ``pack.zip`` (two members under one
    folder), and the top-level file ``top.md``; every folder's own time is 2 h old,
    the files 30 days old."""
    root = tmp_path / "proj"
    for rel in ("notes/a.md", "notes/b.md", "notes/c.md", "top.md"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 1536)
        os.utime(p, (NOW - 30 * 86400, NOW - 30 * 86400))
    (root / "empty").mkdir()
    with zipfile.ZipFile(root / "pack.zip", "w") as zf:
        zf.writestr("inner/one.txt", "1" * 100)
        zf.writestr("inner/two.txt", "2" * 100)
    for rel in ("notes", "empty", "pack.zip"):
        os.utime(root / rel, (NOW - 2 * 3600, NOW - 2 * 3600))
    return root


@pytest.fixture
def tree(root: Path) -> tuple[FileTreeModel, TreeLister]:
    model = FileTreeModel()
    model.clock = lambda: NOW
    model.reset()
    lister = TreeLister(root)
    model.load("", lister.list_dir(""))
    return model, lister


def row(model: FileTreeModel, path: str) -> dict:
    found = {r["path"]: r for r in model.rows()}
    assert path in found, (path, sorted(found))
    return found[path]


def stamp_of(root: Path, rel: str) -> str:
    return format_stamp((root / rel).stat().st_mtime_ns)


def test_an_unlisted_folder_shows_its_own_modified_time_by_age(root, tree):
    model, _ = tree
    notes = row(model, "notes")
    assert notes["stamp"] == stamp_of(root, "notes")  # the folder's own, not its files'
    assert notes["age"] == 2  # two hours old: under a day
    assert notes["size"] == ""  # nothing more is known until it is listed
    assert row(model, "top.md")["age"] == 3  # the files are a month old


def test_a_listed_folder_counts_the_items_the_listing_shows(root, tree):
    model, lister = tree
    model.expanded.add("notes")
    model.load("notes", lister.list_dir("notes"))
    notes = row(model, "notes")
    assert notes["stamp"] == stamp_of(root, "notes")
    assert notes["size"] == "3 items · 4.5 KB"  # listed: the count, and the total once known
    # the count is what the listing shows: a file the screener drops is not an item
    screened = TreeLister(root, {"sources": ["."], "screener": ["*.md", "!notes/c.md"]})
    model.load("notes", screened.list_dir("notes"))
    assert row(model, "notes")["size"] == "2 items · 3.0 KB"


def test_an_empty_folder_says_so_once_listed(root, tree):
    model, lister = tree
    assert row(model, "empty")["size"] == ""
    model.load("empty", lister.list_dir("empty"))
    assert row(model, "empty")["size"] == "empty"
    assert row(model, "empty")["stamp"] == stamp_of(root, "empty")


def test_an_archive_shows_its_own_files_time_and_size_then_its_members(root, tree):
    model, lister = tree
    size = (root / "pack.zip").stat().st_size
    pack = row(model, "pack.zip")
    assert pack["stamp"] == stamp_of(root, "pack.zip") and pack["age"] == 2
    assert pack["size"] == format_bytes(size)
    model.expanded.add("pack.zip")
    model.load("pack.zip", lister.list_dir("pack.zip"))
    assert row(model, "pack.zip")["size"] == f"1 item · {format_bytes(size)}"
    # a folder inside it that is not opened yet: no time is known, so the line says so
    inner = row(model, "pack.zip#/inner")
    assert inner["stamp"] == "" and inner["size"] == "not listed yet"
    model.load("pack.zip#/inner", lister.list_dir("pack.zip#/inner"))
    assert row(model, "pack.zip#/inner")["size"] == "2 items · 200 B"


def test_no_row_has_an_empty_details_line(root, tree):
    model, lister = tree
    for p in ("notes", "empty", "pack.zip"):
        model.expanded.add(p)
        model.load(p, lister.list_dir(p))
    for r in model.rows():
        assert r["stamp"] or r["size"], r["path"]


def test_a_folder_touched_since_shows_the_new_time_on_the_next_listing(root, tree):
    """Auto-update and refresh list the loaded folders again: a folder whose own time
    moved (a file arrived in it) is a change, and its row shows the new time."""
    model, lister = tree
    assert model.load("", lister.list_dir("")) is False  # the same again: nothing changed
    (root / "notes" / "d.md").write_bytes(b"new")
    os.utime(root / "notes", (NOW - 60, NOW - 60))
    assert model.load("", lister.list_dir("")) is True
    notes = row(model, "notes")
    assert notes["stamp"] == stamp_of(root, "notes") and notes["age"] == 0


def test_refresh_ages_moves_a_folders_age_on_with_the_clock(root, tree):
    model, _ = tree
    assert row(model, "notes")["age"] == 2
    model.clock = lambda: NOW + 2 * 86400
    model.refresh_ages()
    assert row(model, "notes")["age"] == 3


def test_a_tree_of_explicit_files_dates_a_folder_by_its_newest_file():
    """A tree handed whole files (another tree's picks) has no folder stat: the newest
    file under the folder stands in, with the count and the total."""
    model = FileTreeModel()
    model.clock = lambda: NOW
    stats = {
        "a/x.txt": (1024, int((NOW - 50) * 1e9)),
        "a/y.txt": (1024, int((NOW - 3 * 86400) * 1e9)),
    }
    model.set_paths(list(stats), stats)
    a = row(model, "a")
    assert a["stamp"] == format_stamp(stats["a/x.txt"][1]) and a["age"] == 0
    assert a["size"] == "2 items · 2.0 KB"


def test_the_listing_hands_a_folder_its_own_time_and_never_walks_it(root, monkeypatch):
    """The folder's time comes from the directory entry its parent's listing already
    read: listing the root reads the root alone."""
    lister = TreeLister(root)
    real = os.scandir
    seen: list[str] = []

    def scandir(path=".", *a, **k):
        seen.append(str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(os, "scandir", scandir)
    entries = {e.path: e for e in lister.list_dir("")}
    assert entries["notes"].mtime_ns == (root / "notes").stat().st_mtime_ns
    assert entries["pack.zip"].mtime_ns == (root / "pack.zip").stat().st_mtime_ns
    assert entries["pack.zip"].size == (root / "pack.zip").stat().st_size
    assert [Path(p).name for p in seen] == ["proj"]
