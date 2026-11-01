"""View names (#79, ADR 0018): a view's id is a short pronounceable name the app hands
out. Seam 1: a document goes in through ``submit`` (or a project is opened); what comes
out is the resolved state, in which every view is named."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from doc_helpers import pin

from specto_core import Project
from specto_core.canvas_document import CanvasInvalid, new_canvas_document
from specto_core.canvas_example import worked_example
from specto_core.live_canvas import LiveCanvas
from specto_core.names import (
    NAME_PATTERN,
    all_names,
    fresh_name,
    fresh_names,
    is_name,
)

NAME = re.compile(NAME_PATTERN)


def tree_of(doc: dict) -> str:
    """The file tree's id: found by type, never by a fixed id (#87)."""
    return next(vid for vid, v in doc["views"].items() if v["type"] == "file_tree")


def test_names_are_two_cv_syllables_unique_in_the_canvas_and_deterministic():
    taken = {"kobi", "tamu", "fege"}
    name = fresh_name(taken, "gallery")
    assert NAME.fullmatch(name) and name not in taken
    assert fresh_name(taken, "gallery") == name  # the same canvas and salt: the same name
    assert fresh_name(taken, "video") != name or fresh_name(taken | {name}, "gallery") != name

    three = fresh_names(taken, 3, "new")
    assert len(set(three)) == 3 and not set(three) & taken
    assert all(NAME.fullmatch(n) for n in three)


def test_the_name_pool_reads_well():
    pool = all_names()
    assert len(pool) > 4000
    for bad in ("nazi", "homo", "puta", "kaka", "pipi", "data", "file", "node"):
        assert bad not in pool, bad
    assert all(n[:2] != n[2:] for n in pool)  # no repeated syllable (baba, lulu)
    assert is_name("kobi") and not is_name("v3") and not is_name("kobix") and not is_name("Kobi")
    assert not is_name("files")  # the old fixed id is not a name: a legacy canvas renames it


def test_a_new_view_under_a_placeholder_key_is_named_on_submit_and_its_links_follow():
    live = LiveCanvas.headless("demo")
    doc = live.document()
    doc["views"]["new_gallery"] = {
        "type": "gallery",
        "label": "front frames",
        "layout": {"position": [300, 0], "size": [640, 400]},
    }
    doc["views"]["v9"] = {
        "type": "video",
        "label": "front video",
        "layout": {"position": [960, 0], "size": [640, 400]},
    }
    doc["root_view"] = "v9"
    pin(doc, "v9", lid="l4")
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "selection",
        "source": {"view": tree_of(doc), "port": "selected_files"},
        "target": {"view": "new_gallery", "port": "data"},
        "params": {"screener": ["@images"]},
        "summary": {"sentence": "The gallery shows the images you pick."},
    }
    doc["links"]["l3"] = {
        "kind": "time_map",
        "source": {"view": "v9", "port": "time"},
        "target": {"view": "new_gallery", "port": "time"},
        "params": {"from_timeline": "video", "to_timeline": "stamp"},
        "summary": {"sentence": "The gallery follows the video's time."},
    }

    res = live.submit(doc, origin="agent:s1", label="a gallery and a video")

    assert set(res.renamed) == {"new_gallery", "v9"}
    gallery, video = res.renamed["new_gallery"], res.renamed["v9"]
    assert NAME.fullmatch(gallery) and NAME.fullmatch(video) and gallery != video
    out = live.document()
    assert set(out["views"]) == {tree_of(out), gallery, video}
    assert out["root_view"] == video
    assert out["links"]["l2"]["target"] == {"view": gallery, "port": "data"}
    assert out["links"]["l3"]["source"] == {"view": video, "port": "time"}
    assert set(res.views["added"]) == {gallery, video}

    # a resubmit of what the agent now reads renames nothing
    again = live.submit(live.document(), origin="agent:s1")
    assert again.empty and again.renamed == {}


def test_a_name_the_canvas_does_not_have_is_kept_when_it_is_in_name_form():
    live = LiveCanvas.headless("demo")
    doc = live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "front"}
    pin(doc, "kobi")
    res = live.submit(doc, origin="agent:s1")
    assert res.renamed == {} and "kobi" in live.document()["views"]


def test_a_placeholder_key_is_rejected_on_plain_validation_with_a_fixit():
    from specto_core.canvas_document import validate_canvas

    doc = new_canvas_document("demo")
    doc["views"]["v2"] = {"type": "gallery"}
    pin(doc, "v2")
    with pytest.raises(CanvasInvalid) as e:
        validate_canvas(doc)
    (problem,) = e.value.problems
    assert problem["path"] == "/views/v2" and "name" in problem["fix"]


def test_a_legacy_canvas_is_renamed_once_on_open_with_one_journal_entry(tmp_path: Path):
    project = Project.init(tmp_path / "proj", "demo")
    legacy = worked_example()
    # the document as a canvas saved before #79: v<n> ids everywhere
    text = json.dumps(legacy)
    for i, name in enumerate(legacy["views"], start=1):
        text = text.replace(f'"{name}"', f'"v{i}"')
    project.canvas_file.write_text(text, encoding="utf-8")

    live = LiveCanvas.open(project)

    doc = live.document()
    assert len(doc["views"]) == len(legacy["views"])
    assert all(NAME.fullmatch(vid) for vid in doc["views"])
    assert set(live.renamed) == {f"v{i}" for i in range(1, len(legacy["views"]) + 1)}
    assert live.renamed["v1"] == tree_of(doc)  # the file tree gets a name too (#87)
    refs = {ln["source"]["view"] for ln in doc["links"].values()} | {
        ln["target"]["view"] for ln in doc["links"].values()
    }
    assert refs <= set(doc["views"]) | {"project"}
    assert doc["root_view"] == live.renamed["v4"]
    assert live.undo_info()["depth"] == 0  # not an undo step
    entries = live.journal.read()
    assert [e.kind for e in entries] == ["rename"]
    assert json.loads(project.canvas_file.read_text(encoding="utf-8"))["views"] == doc["views"]

    # the next open finds names and renames nothing
    again = LiveCanvas.open(project)
    assert again.renamed == {} and [e.kind for e in again.journal.read()] == ["rename"]
    assert again.document() == doc


# -- #87: the file tree is named like every other view ------------------------------------------
def test_a_fresh_canvas_names_its_file_tree_like_any_view():
    doc = new_canvas_document("demo")
    tree = tree_of(doc)
    assert is_name(tree)
    assert doc["links"]["l1"]["target"] == {"view": tree, "port": "data"}
    assert new_canvas_document("other") == {**doc, "project": {"label": "other"}}  # stable
    # the old fixed id is not a view id any more: plain validation refuses it with the
    # name fix-it, a submit names it like any placeholder
    from specto_core.canvas_document import validate_canvas

    old = new_canvas_document("demo")
    old["views"]["files"] = old["views"].pop(tree)
    old["links"]["l1"]["target"]["view"] = "files"
    with pytest.raises(CanvasInvalid) as e:
        validate_canvas(old)
    problem = next(p for p in e.value.problems if p["path"] == "/views/files")
    assert "name" in problem["fix"]


def test_a_canvas_with_the_files_id_is_renamed_on_open_in_the_one_rewrite_entry(tmp_path: Path):
    """A canvas saved between #79 and #87: the file tree is ``files``, the project
    intaker comes off ``project.folder`` and a pinned gallery off ``files.all_files``.
    Opening it names the tree, rewires the links and writes one journal entry."""
    project = Project.init(tmp_path / "proj", "demo")
    old = worked_example()
    tree = tree_of(old)
    text = json.dumps(old).replace(f'"{tree}"', '"files"')
    old = json.loads(text)
    old["links"]["l1"]["source"] = {"view": "project", "port": "folder"}
    old["links"]["l2"]["source"] = {"view": "files", "port": "all_files"}
    project.canvas_file.write_text(json.dumps(old), encoding="utf-8")

    live = LiveCanvas.open(project)

    doc = live.document()
    named = tree_of(doc)
    assert is_name(named) and "files" not in doc["views"] and live.renamed == {"files": named}
    assert doc["links"]["l1"]["target"] == {"view": named, "port": "data"}
    assert doc["links"]["l2"]["source"] == {"view": "project", "port": "all_files"}
    assert doc["links"]["l13"]["source"] == {"view": named, "port": "selected_files"}
    (entry,) = live.journal.read()
    assert entry.kind == "rewire" and entry.renamed == {"files": named}
    assert entry.rewired == ["l1", "l2"]
    assert "renamed 1 view" in entry.label and "moved 2 links" in entry.label
    assert live.undo_info()["depth"] == 0
    again = LiveCanvas.open(project)
    assert again.renamed == {} and len(again.journal.read()) == 1
