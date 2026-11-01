"""Sources, keys, grouping, sort, explain, content hash and the item cache."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from intake_helpers import ALIASES, CAM_A, CAM_B, EXT_KINDS, RUN, make_tree, needs_testdata

from specto_core.errors import NotFound
from specto_core.kinds import default_plugins
from specto_intake import (
    ItemCache,
    SourceSpec,
    content_hash,
    explain,
    natural_key,
    scan,
    scan_env,
    walk_source,
)


def _scan(spec: dict, base: Path):
    return scan(spec, base=base, aliases=ALIASES, ext_kinds=EXT_KINDS)


# -- sources ------------------------------------------------------------------------------


def test_walk_file_dir_glob_depth(tmp_path: Path):
    root = make_tree(
        tmp_path,
        {"a.jpg": "", "d1/b.jpg": "", "d1/d2/c.jpg": "", "d1/d2/d3/d.png": "", "one.txt": "x"},
    )
    rels = lambda src: [f.rel for f in walk_source(0, SourceSpec.coerce(src), root)]  # noqa: E731
    assert rels("one.txt") == ["one.txt"]
    assert rels(".") == ["a.jpg", "one.txt", "d1/b.jpg", "d1/d2/c.jpg", "d1/d2/d3/d.png"]
    assert rels({"root": ".", "depth": 0}) == ["a.jpg", "one.txt"]
    assert rels({"root": ".", "depth": 1}) == ["a.jpg", "one.txt", "d1/b.jpg"]
    assert rels("**/*.jpg") == ["a.jpg", "d1/b.jpg", "d1/d2/c.jpg"]
    assert rels("d1/**/*.png") == ["d2/d3/d.png"]
    f = next(walk_source(0, SourceSpec(root="d1"), root))
    assert f.root.endswith("/d1") and f.rel == "b.jpg" and os.path.isabs(f.path)
    with pytest.raises(NotFound):
        list(walk_source(0, SourceSpec(root="missing"), root))


@pytest.mark.skipif(os.name != "nt" and False, reason="symlinks")
def test_follow_links(tmp_path: Path):
    real = make_tree(tmp_path / "real", {"x.jpg": ""})
    root = tmp_path / "root"
    root.mkdir()
    try:
        os.symlink(real, root / "link", target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    off = [f.rel for f in walk_source(0, SourceSpec(root=str(root)), tmp_path)]
    on = [f.rel for f in walk_source(0, SourceSpec(root=str(root), follow_links=True), tmp_path)]
    assert off == [] and on == ["link/x.jpg"]


# -- keys, grouping, sort ---------------------------------------------------------------


def test_sidecars_group_without_keys_regex(tmp_path: Path):
    root = make_tree(
        tmp_path, {"f/001.jpg": "", "f/001.json": "{}", "f/002.jpg": "", "f/010.jpg": ""}
    )
    r = _scan({"sources": ["f"]}, root)
    assert [it.key for it in r.items] == ["001", "002", "010"]
    assert sorted(r.items[0].parts) == [".jpg", ".json"]
    assert r.items[1].parts.keys() == {".jpg"}
    assert r.kinds == {"image2d": 3, "json": 1}
    assert r.files == 4 and r.skipped == []
    assert [it.index for it in r.items] == [0, 1, 2]


def test_natural_sort_and_key_sort(tmp_path: Path):
    root = make_tree(
        tmp_path,
        {"frame_10.png": "", "frame_9.png": "", "frame_100.png": "", "Frame_2.png": ""},
    )
    r = _scan({"sources": ["."]}, root)
    assert [it.key for it in r.items] == ["Frame_2", "frame_9", "frame_10", "frame_100"]
    r = _scan({"sources": ["."], "keys": r"(?i)frame_(?P<n>\d+)", "sort": "key"}, root)
    assert [it.key for it in r.items] == ["2", "9", "10", "100"]
    assert natural_key("a10b2") == ("a", 10, "b", 2, "")


def test_mtime_sort(tmp_path: Path):
    root = make_tree(tmp_path, {"a.txt": "", "b.txt": "", "c.txt": ""})
    now = time.time()
    os.utime(root / "a.txt", (now, now + 30))
    os.utime(root / "b.txt", (now, now + 10))
    os.utime(root / "c.txt", (now, now + 20))
    r = _scan({"sources": ["."], "sort": "mtime"}, root)
    assert [it.key for it in r.items] == ["b", "c", "a"]


def test_keys_regex_skips_non_matching_and_groups_by_fields(tmp_path: Path):
    root = make_tree(
        tmp_path,
        {
            "run_A/frame_001.png": "",
            "run_A/frame_001.json": "{}",
            "run_A/frame_002.png": "",
            "run_B/frame_001.png": "",
            "run_A/notes.md": "#",
        },
    )
    spec = {"sources": ["."], "keys": r"(?P<run>run_\w+)/frame_(?P<frame>\d+)"}
    r = _scan(spec, root)
    assert [it.key for it in r.items] == ["run_A/001", "run_A/002", "run_B/001"]
    assert r.items[0].meta["keys"] == {"run": "run_A", "frame": "001"}
    assert [s.reason for s in r.skipped] == ["keys regex did not match"]
    assert r.skipped[0].path.endswith("run_A/notes.md")
    # group by frame only: A and B frame 001 become one item with two png roles
    r = _scan({**spec, "group_by": ["frame"]}, root)
    assert [it.key for it in r.items] == ["001", "002"]
    roles = sorted(r.items[0].parts)
    assert roles == [".json", ".png", "run_B/.png"]


def test_multiple_sources_namespace_keys_unless_merge_roots(tmp_path: Path):
    root = make_tree(tmp_path, {"a/1.jpg": "", "b/1.jpg": "", "b/2.jpg": ""})
    r = _scan({"sources": ["a", "b"]}, root)
    assert [it.key for it in r.items] == ["0:1", "1:1", "1:2"]
    r = _scan({"sources": ["a", "b"], "merge_roots": True}, root)
    assert [it.key for it in r.items] == ["1", "2"]
    assert sorted(r.items[0].parts) == [".jpg", "b/.jpg"]
    assert r.sources == [
        {"index": 0, "root": "a", "files": 1},
        {"index": 1, "root": "b", "files": 2},
    ]


# -- explain ------------------------------------------------------------------------------


def test_explain_names_source_rule_and_key(tmp_path: Path):
    root = make_tree(tmp_path, {"f/001.jpg": "", "f/001.tmp": "", "g/x.jpg": ""})
    spec = {"sources": ["f"], "screener": ["@images", "!*.tmp"], "keys": r"(?P<n>\d+)"}
    ex = explain(spec, root / "f" / "001.jpg", base=root, aliases=ALIASES)
    assert ex["included"] and ex["source"] == 0 and ex["rel"] == "001.jpg"
    assert ex["item_key"] == "001" and ex["keys"] == {"n": "001"} and ex["role"] == ".jpg"
    assert ex["screener"]["deciding_rule"]["index"] == 0
    ex = explain(spec, "f/001.tmp", base=root, aliases=ALIASES)
    assert not ex["included"] and "rule 1" in ex["reason"]
    ex = explain(spec, root / "g" / "x.jpg", base=root, aliases=ALIASES)
    assert not ex["included"] and ex["reason"] == "not under any source root"
    ex = explain({"sources": ["f/*.png"]}, "f/001.jpg", base=root, aliases=ALIASES)
    assert not ex["included"] and "source pattern" in ex["reason"]
    ex = explain(
        {"sources": ["f"], "keys": r"(?P<n>[a-z]{4,})"}, "f/001.jpg", base=root, aliases=ALIASES
    )
    assert not ex["included"] and ex["reason"] == "keys regex did not match"


# -- content hash and cache --------------------------------------------------------------


def test_content_hash_tracks_files_and_mtimes(tmp_path: Path):
    root = make_tree(tmp_path, {"a.jpg": "x", "b.jpg": "y"})
    h1 = _scan({"sources": ["."]}, root).content_hash
    assert _scan({"sources": ["."]}, root).content_hash == h1 and len(h1) == 32
    (root / "c.jpg").write_text("z")
    h2 = _scan({"sources": ["."]}, root).content_hash
    assert h2 != h1
    (root / "c.jpg").unlink()
    assert _scan({"sources": ["."]}, root).content_hash == h1
    os.utime(root / "a.jpg", (1, 1))
    assert _scan({"sources": ["."]}, root).content_hash != h1


def test_item_cache_round_trip_and_prune(tmp_path: Path):
    root = make_tree(tmp_path / "data", {"a.jpg": "", "a.json": "{}"})
    r = _scan({"sources": ["."]}, root)
    cache = ItemCache("proj-1", tmp_path / "cache")
    p = cache.write("c1", r.content_hash, r.items)
    assert p == cache.path("c1", r.content_hash) and p.parent.name == "proj-1"
    back = cache.read("c1", r.content_hash)
    assert back == r.items and content_hash(back) == r.content_hash
    assert cache.read("c1", "nope") is None
    cache.write("c1", "old", [])
    assert cache.prune("c1", r.content_hash) == 1 and cache.has("c1", r.content_hash)
    assert ItemCache(None).project_id == "no-project"


def test_env_overrides_cache_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "env-cache"))
    assert ItemCache("p").dir == tmp_path / "env-cache" / "items" / "p"


# -- the real test data ---------------------------------------------------------------


@needs_testdata
def test_cam_a_and_cam_b_keyed_by_frame():
    aliases, ext_kinds = scan_env(default_plugins())
    assert "@images" in aliases and ext_kinds[".jpg"] == ["image2d"]
    spec = {
        "sources": [str(CAM_A)],
        "screener": ["@images", "@json"],
        "keys": r"^(?P<frame>\d{5})_",
    }
    front = scan(spec, base=RUN, aliases=aliases, ext_kinds=ext_kinds)
    rear = scan({**spec, "sources": [str(CAM_B)]}, base=RUN, aliases=aliases, ext_kinds=ext_kinds)
    assert front.item_count == 240 and rear.item_count == 240
    assert front.kinds == {"image2d": 240, "json": 240}
    assert all(sorted(it.parts) == [".jpg", ".json"] for it in front.items)
    assert [it.key for it in front.items] == [it.key for it in rear.items]
    assert front.items[0].key == "00000" and front.items[-1].key == "00239"
    assert front.content_hash != rear.content_hash
    assert front.skipped == [] and front.files == 480


@needs_testdata
def test_whole_run_with_namespaced_sources_and_meta():
    aliases, ext_kinds = scan_env(default_plugins())
    spec = {
        "sources": [str(CAM_A), str(CAM_B), {"root": str(RUN / "meta"), "depth": 0}],
        "screener": ["@images", "@json", "@text", "!*.yaml"],
    }
    r = scan(spec, base=RUN, aliases=aliases, ext_kinds=ext_kinds)
    assert r.item_count == 240 * 2 + 4  # 4 csv files, yaml excluded, md not included
    assert r.items[0].key.startswith("0:") and r.items[240].key.startswith("1:")
    assert r.kinds == {"image2d": 480, "json": 480, "text": 4}
