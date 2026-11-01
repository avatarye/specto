"""The project folder as the agent sees it (#169): the whole folder from the project
root, a summary that keeps the top levels when there are too many folders, and
``list_files`` by folder or glob."""

from __future__ import annotations

from pathlib import Path

import pytest

from specto_core.errors import InvalidArgs, NotFound
from specto_core.project_listing import (
    ProjectListing,
    folder_summary,
    list_files,
    walk_project,
)

#: a project layout in small: recordings mirrored by their annotations
DEPOT = [
    "README.md",
    "recordings/archive/clips/rigB/day1/take1/clip.usda",
    "recordings/archive/clips/rigB/day1/take2/clip.usda",
    "recordings/archive/clips/rigA/day2/take3/clip.usda",
    "annotations/archive/notes/rigB/day1/take1/geo/track.geojson",
    "annotations/archive/notes/rigB/day1/take1/geo/zone.geojson",
    "annotations/archive/notes/rigB/day1/take2/geo/track.geojson",
    "annotations/archive/notes/rigA/day2/take3/geo/track.geojson",
    "annotations/archive/notes/rigA/day2/take3/photo.jpg",
]


@pytest.fixture
def depot(tmp_path: Path) -> Path:
    for rel in [*DEPOT, ".specto/canvas.json", ".specto/links/geo.py"]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    return tmp_path


def test_the_walk_lists_the_whole_folder_without_the_specto_folder(depot: Path):
    assert walk_project(depot) == sorted(DEPOT)


def test_the_listing_is_cached_for_a_few_seconds(depot: Path):
    now = [0.0]
    listing = ProjectListing(depot, ttl_s=5, clock=lambda: now[0])
    assert len(listing.paths()) == len(DEPOT)
    (depot / "late.txt").write_bytes(b"x")
    now[0] = 1.0
    assert len(listing.paths()) == len(DEPOT)
    now[0] = 6.5
    assert "late.txt" in listing.paths()


def test_a_small_project_is_summarised_folder_by_folder():
    s = folder_summary(DEPOT)
    assert s["count"] == len(DEPOT) and "depth" not in s
    assert s["folders"]["."] == {".md": 1}
    assert s["folders"]["annotations/archive/notes/rigA/day2/take3"] == {".jpg": 1}


def test_too_many_folders_keep_the_top_levels_and_fold_the_rest():
    s = folder_summary(DEPOT, max_folders=4)
    # depth 3 is the deepest level with at most 4 folders: the two parallel roots show
    assert s["depth"] == 3
    assert set(s["folders"]) == {".", "annotations/archive/notes", "recordings/archive/clips"}
    assert s["folders"]["annotations/archive/notes"] == {".geojson": 4, ".jpg": 1}
    # rigA, rigB; rigA/day2, rigB/day1; three takes; three geo
    assert s["deeper"]["annotations/archive/notes"] == 10
    assert "list_files" in s["note"]
    assert s["count"] == len(DEPOT)


def test_list_files_shows_one_folder_level_with_subtree_counts():
    out = list_files(DEPOT, "annotations/archive/notes")
    assert out["path"] == "annotations/archive/notes" and out["total_files"] == 5
    assert out["folders"] == [
        {"path": "annotations/archive/notes/rigA", "files": 2, "folders": 1},
        {"path": "annotations/archive/notes/rigB", "files": 3, "folders": 1},
    ]
    assert out["files"] == [] and out["truncated"] is False
    root = list_files(DEPOT)
    assert [f["path"] for f in root["folders"]] == ["annotations", "recordings"]
    assert root["files"] == ["README.md"]


def test_list_files_by_glob_finds_files_at_any_depth():
    out = list_files(DEPOT, "annotations", glob="*.geojson", limit=2)
    assert out["matches"] == 4 and len(out["files"]) == 2 and out["truncated"] is True
    assert out["files"][0].startswith("annotations/archive/notes/")
    out = list_files(DEPOT, glob="**/geo/*.geojson")
    assert out["matches"] == 4
    out = list_files(DEPOT, glob="recordings/**/clip.usda")
    assert out["matches"] == 3


def test_a_wrong_path_names_the_closest_folder_that_exists():
    """The agent guessed source/records; the fix-it shows what source holds."""
    with pytest.raises(NotFound) as e:
        list_files(DEPOT, "annotations/notes")
    assert "annotations has: archive" in e.value.fix
    with pytest.raises(InvalidArgs):
        list_files(DEPOT, "README.md")
    # the case of a folder name does not have to match
    assert list_files(DEPOT, "ANNOTATIONS/ARCHIVE")["path"] == "annotations/archive"
