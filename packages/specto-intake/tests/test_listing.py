"""Seam 1 (#231): the file tree's folder-at-a-time listing. One level per call, over
the intaker's sources and screener; the files under a picked folder as a recursive scan
would list them; archives as folders; no walk of the whole project to show a row."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest
from intake_helpers import ALIASES

from specto_intake import sources
from specto_intake.listing import Entry, TreeLister
from specto_intake.scan import scan


def make_tree(root: Path) -> None:
    files = {
        "runs/cam-a/00000.jpg": b"a" * 10,
        "runs/cam-a/00000.json": b"{}",
        "runs/cam-a/00001.jpg": b"b" * 20,
        "runs/cam-b/00000.jpg": b"c" * 30,
        "notes/readme.md": b"# hi",
        "notes/deep/er/still/note.md": b"x",
        "top.txt": b"t",
        ".specto/canvas.json": b"{}",
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (root / "empty").mkdir()
    with zipfile.ZipFile(root / "runs" / "drive.zip", "w") as zf:
        zf.writestr("README.txt", "r")
        zf.writestr("front/00042.jpg", "j" * 5)
        zf.writestr("front/00042.json", "{}")
        zf.writestr("front/sub/00043.jpg", "k")


def names(entries: list[Entry]) -> list[tuple[str, bool]]:
    return [(e.path, e.is_dir) for e in entries]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    make_tree(tmp_path / "proj")
    return tmp_path / "proj"


def test_the_root_lists_one_level_folders_first_and_never_the_projects_specto(root: Path):
    lister = TreeLister(root)
    assert names(lister.list_dir("")) == [
        ("empty", True),
        ("notes", True),
        ("runs", True),
        ("top.txt", False),
    ]
    # files carry the stat the listing saw; folders carry none (not known cheaply)
    top = lister.list_dir("")[-1]
    assert (top.size, top.mtime_ns) == (1, (root / "top.txt").stat().st_mtime_ns)
    assert names(lister.list_dir("runs")) == [
        ("runs/cam-a", True),
        ("runs/cam-b", True),
        ("runs/drive.zip", True),  # an archive is a folder
    ]
    assert [e.archive for e in lister.list_dir("runs")] == [False, False, True]
    assert names(lister.list_dir("runs/cam-a")) == [
        ("runs/cam-a/00000.jpg", False),
        ("runs/cam-a/00000.json", False),
        ("runs/cam-a/00001.jpg", False),
    ]
    assert lister.list_dir("empty") == []
    assert lister.list_dir("nowhere") == [] and lister.list_dir(".specto") == []


def test_an_archive_lists_its_members_one_level_at_a_time(root: Path):
    lister = TreeLister(root)
    assert names(lister.list_dir("runs/drive.zip")) == [
        ("runs/drive.zip#/front", True),
        ("runs/drive.zip#/README.txt", False),
    ]
    assert names(lister.list_dir("runs/drive.zip#/front")) == [
        ("runs/drive.zip#/front/sub", True),
        ("runs/drive.zip#/front/00042.jpg", False),
        ("runs/drive.zip#/front/00042.json", False),
    ]
    member = lister.list_dir("runs/drive.zip#/front")[1]
    assert member.size == 5 and not member.is_dir
    paths, _ = lister.files_under(["runs/drive.zip#/front"])
    assert paths == [
        "runs/drive.zip#/front/00042.jpg",
        "runs/drive.zip#/front/00042.json",
        "runs/drive.zip#/front/sub/00043.jpg",
    ]


def test_the_screener_and_a_source_pattern_apply_to_files_not_folders(root: Path):
    lister = TreeLister(root, {"sources": ["."], "screener": ["@images"]}, ALIASES)
    assert names(lister.list_dir("")) == [("empty", True), ("notes", True), ("runs", True)]
    assert names(lister.list_dir("runs/cam-a")) == [
        ("runs/cam-a/00000.jpg", False),
        ("runs/cam-a/00001.jpg", False),
    ]
    assert names(lister.list_dir("runs/drive.zip#/front")) == [
        ("runs/drive.zip#/front/sub", True),
        ("runs/drive.zip#/front/00042.jpg", False),
    ]
    glob = TreeLister(root, {"sources": ["runs/**/*.json"]})
    assert names(glob.list_dir("")) == [("runs", True)]  # only the way down to the source
    assert names(glob.list_dir("runs")) == [
        ("runs/cam-a", True),
        ("runs/cam-b", True),
        ("runs/drive.zip", True),
    ]
    assert names(glob.list_dir("runs/cam-a")) == [("runs/cam-a/00000.json", False)]
    assert names(glob.list_dir("notes")) == []  # outside every source


def test_files_under_matches_the_recursive_scan_for_every_folder_and_pick(root: Path):
    """What a folder pick stands for is exactly what the intaker's own walk finds."""
    for spec in (
        {"sources": ["."]},
        {"sources": ["."], "screener": ["@images", "!*.json"]},
        {"sources": ["runs"], "screener": ["@json"]},
        {"sources": [{"root": "notes", "depth": 1}]},
    ):
        lister = TreeLister(root, spec, ALIASES)
        result = scan(spec, base=root, aliases=ALIASES, ext_kinds={})
        # the scan lists .specto/ too; the intaker's resolver drops it, as the lister does
        walked = sorted(
            rel
            for it in result.items
            for p in it.parts.values()
            if not (rel := os.path.relpath(p, root).replace("\\", "/")).startswith(".specto/")
        )
        paths, stats = lister.files_under([""])
        assert paths == walked, spec
        assert set(stats) == set(paths)
        # the same files reached folder by folder
        picked: list[str] = []
        for top in lister.list_dir(""):
            picked.extend(lister.files_under([top.path])[0])
        assert sorted(picked) == walked, spec
    lister = TreeLister(root)
    paths, stats = lister.files_under(["notes/readme.md", "runs/cam-b"])
    assert paths == ["notes/readme.md", "runs/cam-b/00000.jpg"]
    assert stats["runs/cam-b/00000.jpg"][0] == 30
    assert lister.files_under(["nowhere", "empty"]) == ([], {})


def test_files_under_stops_between_folders_when_cancelled(root: Path):
    lister = TreeLister(root)
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    paths, _ = lister.files_under(["runs", "notes"], cancelled)
    assert len(paths) < 7  # the whole tree has more


def test_a_source_outside_the_project_is_listed_under_its_absolute_path(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (tmp_path / "elsewhere" / "data").mkdir(parents=True)
    (tmp_path / "elsewhere" / "data" / "a.txt").write_bytes(b"a")
    (proj / "own.txt").write_bytes(b"o")
    outside = (tmp_path / "elsewhere" / "data").resolve()
    lister = TreeLister(proj, {"sources": [".", str(outside)]})
    posix = str(outside).replace("\\", "/")
    parts = posix.split("/")
    top = lister.list_dir("")
    assert (parts[0], True) in names(top) and ("own.txt", False) in names(top)
    # down the chain of the absolute path, then the folder itself
    chain = "/".join(parts[:-1])
    assert names(lister.list_dir(chain)) == [(posix, True)]
    assert names(lister.list_dir(posix)) == [(posix + "/a.txt", False)]
    paths, _ = lister.files_under([parts[0]])
    assert paths == [posix + "/a.txt"]
    assert lister.files_under([""])[0] == sorted(["own.txt", posix + "/a.txt"])


def test_listing_a_folder_never_walks_the_tree(root: Path, monkeypatch: pytest.MonkeyPatch):
    def no_walk(*a, **k):
        raise AssertionError("walk_source called")

    monkeypatch.setattr(sources, "walk_source", no_walk)
    lister = TreeLister(root)
    assert len(lister.list_dir("")) == 4
    assert len(lister.list_dir("runs/cam-a")) == 3
