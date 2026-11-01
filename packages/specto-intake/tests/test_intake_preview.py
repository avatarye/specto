"""intake.preview / intake.keys_preview: pure, read-only, bounded, errors as data."""

from __future__ import annotations

from pathlib import Path

import pytest
from intake_helpers import ALIASES, EXT_KINDS, copy_frames, make_tree, needs_testdata

import specto_intake  # noqa: F401  (registers the commands)
from specto_core import Engine, Project
from specto_core.registry import registry
from specto_intake.preview import guess_keys, keys_preview, preview, sample_names


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.init(tmp_path / "proj", "demo")


@pytest.fixture
def engine(project: Project) -> Engine:
    return Engine(project=project)


def test_preview_commands_are_registered_read_only():
    for n in ("intake.preview", "intake.keys_preview"):
        assert n in registry.names()
        assert registry.get(n).meta.read_only and registry.get(n).meta.scope == "global"


@needs_testdata
def test_preview_counts_rules_items_and_creates_nothing(engine: Engine, project: Project):
    copy_frames(project.root / "runs" / "cam-a", 4)
    make_tree(project.root / "runs" / "cam-a", {"notes.txt": "x", "sub/deep.png": ""})
    r = engine.call(
        "intake.preview",
        {
            "spec": {
                "sources": ["runs/cam-a"],
                "screener": ["@images", "@json", "!sub/**"],
                "keys": r"(?:^|/)(?P<frame>\d{5})_",
            },
            "limit": 2,
        },
    )
    assert r["ok"] and r["errors"] == [] and r["job"] is None
    assert r["files"] == 10 and r["matched_count"] == 8 and r["excluded_count"] == 2
    assert r["matched"] == ["00000_1700000000000000000.jpg", "00000_1700000000000000000.json"]
    by_path = {e["path"]: e for e in r["excluded"]}
    assert (
        by_path["notes.txt"]["rule"] == "" and "no rule matched" in by_path["notes.txt"]["reason"]
    )
    assert by_path["sub/deep.png"]["rule"] == "!sub/**"
    assert r["item_count"] == 4 and len(r["items"]) == 2
    assert r["items"][0] == {
        "key": "00000",
        "keys": {"frame": "00000"},
        "parts": {
            ".jpg": "00000_1700000000000000000.jpg",
            ".json": "00000_1700000000000000000.json",
        },
    }
    assert r["key_fields"] == ["frame"] and r["kinds"] == {"image2d": 4, "json": 4}
    assert r["parts"] == {".jpg": 4, ".json": 4}
    # nothing landed in the canvas document or the undo history
    assert list(engine.live.data["links"]) == ["l1"] and engine.live.history == []


def test_preview_reports_errors_instead_of_raising(engine: Engine, project: Project):
    r = engine.call("intake.preview", {"spec": {"sources": ["runs/missing"]}})
    assert not r["ok"] and "does not exist" in r["errors"][0]
    copy_frames(project.root / "runs" / "cam-a", 1)
    r = engine.call("intake.preview", {"spec": {"sources": ["runs/cam-a"], "screener": ["@x"]}})
    assert not r["ok"] and "@images" in r["errors"][0]
    r = engine.call("intake.preview", {"spec": {"sources": ["runs/cam-a"], "keys": r"^\d+"}})
    assert not r["ok"] and "named groups" in r["errors"][0]
    r = engine.call("intake.preview", {"spec": {"sources": ["runs/cam-a"], "keys": r"^(?P<a>"}})
    assert not r["ok"] and "does not compile" in r["errors"][0]


@needs_testdata
def test_preview_stops_early_and_counts_skipped(engine: Engine, project: Project):
    copy_frames(project.root / "runs" / "cam-a", 5)
    make_tree(project.root / "runs" / "cam-a", {"readme.json": "{}"})
    r = engine.call(
        "intake.preview",
        {"spec": {"sources": ["runs/cam-a"], "keys": r"(?:^|/)(?P<frame>\d{5})_"}, "max_files": 4},
    )
    assert r["truncated"] and r["files"] == 4 and r["item_count"] == 2
    r = engine.call(
        "intake.preview", {"spec": {"sources": ["runs/cam-a"], "keys": r"(?:^|/)(?P<frame>\d{5})_"}}
    )
    assert not r["truncated"] and r["skipped_count"] == 1
    assert r["skipped"][0]["path"].endswith("readme.json")


@needs_testdata
def test_preview_background_runs_as_a_job(engine: Engine, project: Project):
    copy_frames(project.root / "runs" / "cam-a", 2)
    r = engine.call("intake.preview", {"spec": {"sources": ["runs/cam-a"]}, "background": True})
    assert r["job"] == "j1" and r["ok"] and r["item_count"] == 2  # inline manager: done already
    st = engine.call("job.status", {"job": "j1"})["job"]
    assert st["state"] == "done" and st["kind"] == "intake.preview"
    assert st["result"]["item_count"] == 2


def test_keys_preview_table_presets_and_errors(engine: Engine):
    names = ["00012_1757.jpg", "00012_1757.json", "front/00013_1.jpg", "readme.md"]
    r = engine.call("intake.keys_preview", {"regex": r"^(?P<frame>\d{5})_", "names": names})
    assert r["fields"] == ["frame"] and r["matched"] == 2 and r["unmatched"] == 2
    assert r["rows"][0] == {"name": names[0], "matched": True, "captures": {"frame": "00012"}}
    assert r["rows"][2]["matched"] is False and r["error"] is None
    assert {p["label"] for p in r["presets"]} >= {"leading number", "frame NNNNN_"}
    r = engine.call("intake.keys_preview", {"regex": r"^\d+", "names": names})
    assert "no named group" in r["error"] and r["rows"] == []
    r = engine.call("intake.keys_preview", {"regex": r"(?P<a>", "names": names})
    assert "does not compile" in r["error"]
    r = engine.call("intake.keys_preview", {"names": names[:1]})  # default stem key
    assert r["fields"] == ["stem"] and r["rows"][0]["captures"] == {"stem": "00012_1757"}
    r = engine.call(
        "intake.keys_preview",
        {"regex": r"^(?P<camera>[^/]+)/(?P<frame>\d+)", "names": ["front/00013_1.jpg"]},
    )
    assert r["rows"][0]["captures"] == {"camera": "front", "frame": "00013"}


def test_preview_function_and_helpers(tmp_path: Path):
    root = make_tree(tmp_path, {"a/1.jpg": "", "a/1.json": "", "a/2.jpg": ""})
    out = preview({"sources": ["a"]}, base=root, aliases=ALIASES, ext_kinds=EXT_KINDS)
    assert out["ok"] and out["item_count"] == 2 and out["parts"] == {".jpg": 2, ".json": 1}
    assert keys_preview("", ["a/1.jpg"])["rows"][0]["captures"] == {"stem": "a/1"}
    assert guess_keys(["00001_x.jpg", "00001_x.json", "00002_y.jpg"]) == r"(?:^|/)(?P<frame>\d{5})_"
    assert guess_keys(["0001_x.jpg", "00002_y.jpg"]) is None  # widths differ
    assert guess_keys(["00001_x.jpg", "00002_y.jpg", "00003_z.jpg", "0004_w.jpg"]) is None
    assert (
        guess_keys(["00001_x.jpg"] * 8 + ["readme.txt"]) == r"(?:^|/)(?P<frame>\d{5})_"
    )  # a stray
    assert guess_keys(["a.jpg", "b.jpg"]) is None
    assert guess_keys(["7-a.jpg", "8-b.jpg"]) == r"(?:^|/)(?P<frame>\d{1})"
    assert sample_names(root / "a") == ["1.jpg", "1.json", "2.jpg"]
    assert sample_names(root / "a" / "1.jpg") == ["1.jpg"]
    assert sample_names(root / "nope") == []
