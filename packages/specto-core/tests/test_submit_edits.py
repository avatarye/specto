"""Partial submits (#98, ADR 0013 amendment): ``submit(edits=...)`` takes a JSON Merge
Patch (RFC 7396) over the live document. Seam 1: the edit goes in through ``submit``;
what comes out is the resolved document, the undo step and the result, exactly as for
a whole document."""

from __future__ import annotations

from pathlib import Path

import pytest
from doc_helpers import pin

from specto_core import Project
from specto_core.canvas_document import CanvasInvalid
from specto_core.edits import merge_edits
from specto_core.live_canvas import LiveCanvas

AGENT = "agent:prompt"


@pytest.fixture
def live() -> LiveCanvas:
    """The file tree (``fege``) and a front gallery ``kobi`` pinned from the project."""
    live = LiveCanvas.headless("demo")
    doc = live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "front frames",
        "layout": {"position": [900, 420], "size": [620, 400]},
        "params": {"tile": 132},
    }
    pin(doc, "kobi", sources=["runs/front"])
    live.submit(doc, origin="user", label="setup")
    return live


def test_a_move_names_only_the_position_and_everything_else_stays(live: LiveCanvas):
    before = live.document()

    res = live.submit(
        edits={"views": {"kobi": {"layout": {"position": [330, 0]}}}},
        origin=AGENT,
        label="move kobi next to the file tree",
    )

    after = live.document()
    assert after["views"]["kobi"]["layout"] == {"position": [330.0, 0.0], "size": [620.0, 400.0]}
    after["views"]["kobi"]["layout"]["position"] = before["views"]["kobi"]["layout"]["position"]
    assert after == before
    assert res.views == {"added": [], "removed": [], "changed": ["kobi"]}
    assert res.label == "move kobi next to the file tree" and res.touched == ["kobi"]


def test_a_param_merges_into_the_params_and_a_list_is_replaced_whole(live: LiveCanvas):
    live.submit(
        edits={
            "views": {"kobi": {"params": {"sort": "name"}}},
            "links": {"l2": {"params": {"sources": ["runs/rear", "runs/side"]}}},
        },
        origin=AGENT,
    )
    live.submit(edits={"links": {"l2": {"params": {"sources": ["runs/rear"]}}}}, origin=AGENT)

    doc = live.document()
    assert doc["views"]["kobi"]["params"]["tile"] == 132  # untouched by the merge
    assert doc["views"]["kobi"]["params"]["sort"] == "name"
    assert doc["links"]["l2"]["params"]["sources"] == ["runs/rear"]


def test_null_on_a_nullable_field_is_the_same_document_as_its_default(live: LiveCanvas):
    """Every nullable field defaults to None, so removing the key (null in an edit)
    normalises to the document that says null outright."""
    live.submit(edits={"links": {"l2": {"params": {"keys": r"(?P<frame>\d+)"}}}}, origin=AGENT)
    explicit = live.document()
    explicit["links"]["l2"]["params"]["keys"] = None
    explicit_doc = LiveCanvas(explicit).document()

    live.submit(edits={"links": {"l2": {"params": {"keys": None}}}}, origin=AGENT)

    assert live.document()["links"]["l2"] == explicit_doc["links"]["l2"]


def gallery_edits(tree: str = "fege") -> dict:
    """The spec's second worked prompt, "show the selected folder as a gallery": a new
    view and its link under placeholder keys, omitted fields taking their defaults."""
    return {
        "views": {
            "new1": {
                "type": "gallery",
                "label": "picked images",
                "layout": {"position": [330, 0], "size": [540, 400]},
            }
        },
        "links": {
            "new2": {
                "kind": "intaker",
                "scope": "selection",
                "label": "picked images",
                "source": {"view": tree, "port": "selected_files"},
                "target": {"view": "new1", "port": "data"},
                "params": {"sources": ["."], "screener": ["@images"]},
                "summary": {"sentence": "Shows the images you pick in the file tree."},
            }
        },
    }


def test_a_new_view_and_link_under_placeholders_get_a_name_and_the_next_link_id(
    live: LiveCanvas,
):
    res = live.submit(edits=gallery_edits(), origin=AGENT, label="add a gallery")

    name = res.renamed["new1"]
    assert res.renamed == {"new1": name, "new2": "l3"}
    doc = live.document()
    assert doc["views"][name]["type"] == "gallery"
    assert doc["links"]["l3"]["target"] == {"view": name, "port": "data"}
    assert set(doc["links"]) == {"l1", "l2", "l3"}
    assert res.views["added"] == [name] and res.links["added"] == ["l3"]


def test_removing_a_view_removes_its_links_and_clears_the_root_view(live: LiveCanvas):
    """The same cascade as the close gesture: null on a view takes every link into or
    out of it along, and the root view when it was that view; links.removed lists them."""
    video = {
        "views": {
            "new1": {
                "type": "video",
                "label": "front video",
                "layout": {"position": [330, 0], "size": [540, 400]},
            }
        },
        "links": {
            "new2": {
                "kind": "intaker",
                "scope": "pinned",
                "source": {"view": "project", "port": "all_files"},
                "target": {"view": "new1", "port": "data"},
                "params": {"sources": ["runs/front.mp4"]},
                "summary": {"sentence": "Always shows the front video."},
            }
        },
        "root_view": "new1",
    }
    added = live.submit(edits=video, origin=AGENT)
    player = added.renamed["new1"]
    assert live.data["root_view"] == player

    res = live.submit(edits={"views": {player: None}}, origin=AGENT, label="close the video")

    doc = live.document()
    assert player not in doc["views"] and "kobi" in doc["views"]
    assert set(doc["links"]) == {"l1", "l2"} and doc["root_view"] is None
    assert res.views["removed"] == [player] and res.links["removed"] == ["l3"]


@pytest.mark.parametrize(
    ("edits", "path"),
    [
        ({"schema_version": 3}, "/schema_version"),
        ({"window_frame": {"rect": {"x": 0, "y": 0, "w": 10, "h": 10}}}, "/window_frame"),
        ({"views": None}, "/views"),
        ({"links": None}, "/links"),
        ({"project": None}, "/project"),
        ({"project": {"label": "x", "root": "C:/"}}, "/project/root"),
        ({"layout": {}}, "/layout"),
        ([{"op": "replace"}], ""),
        ('{"views": {"kobi": {"label": "a"}, "kobi": {"label": "b"}}}', "/views"),
        ("{not json", ""),
    ],
)
def test_a_refused_edit_changes_nothing_and_says_how_to_fix_it(live: LiveCanvas, edits, path):
    before = live.document()
    depth, journal = len(live.history), len(live.journal.read())

    with pytest.raises(CanvasInvalid) as e:
        live.submit(edits=edits, origin=AGENT)

    assert [p["path"] for p in e.value.problems] == [path]
    assert all(p["fix"] for p in e.value.problems)
    assert live.document() == before
    assert len(live.history) == depth and len(live.journal.read()) == journal


def test_an_edit_may_relabel_the_project(live: LiveCanvas):
    live.submit(edits='{"project": {"label": "run-001 review"}}', origin=AGENT)
    assert live.data["project"]["label"] == "run-001 review"


def test_a_submit_takes_a_document_or_edits_never_both_or_neither(live: LiveCanvas):
    for kw in ({}, {"document": live.document(), "edits": {"root_view": None}}):
        with pytest.raises(CanvasInvalid) as e:
            live.submit(origin=AGENT, **kw)
        assert "document" in e.value.problems[0]["fix"] and "edits" in e.value.problems[0]["fix"]


def test_a_misspelt_view_name_is_no_silent_new_view(live: LiveCanvas):
    """ADR 0018: a name-form key the canvas does not have is a re-add, which an edit
    of one field cannot validate: the fix-it lists the view names."""
    with pytest.raises(CanvasInvalid) as e:
        live.submit(edits={"views": {"kobu": {"layout": {"position": [0, 0]}}}}, origin=AGENT)
    assert any("kobi" in p["fix"] for p in e.value.problems)
    assert "kobu" not in live.data["views"]


def test_locks_and_reachability_bind_an_edit_as_they_bind_a_document(live: LiveCanvas):
    live.submit(edits={"views": {"kobi": {"locked": True}}}, origin="user")
    with pytest.raises(CanvasInvalid) as e:
        live.submit(edits={"views": {"kobi": {"layout": {"position": [0, 0]}}}}, origin=AGENT)
    assert e.value.problems[0]["path"].startswith("/views/kobi")

    with pytest.raises(CanvasInvalid) as e:
        live.submit(edits={"links": {"l2": None}}, origin=AGENT)
    assert any("kobi" in p["message"] for p in e.value.problems)


def test_removing_an_id_the_canvas_does_not_have_is_refused(live: LiveCanvas):
    with pytest.raises(CanvasInvalid) as e:
        live.submit(edits={"views": {"kobu": None}, "links": {"l9": None}}, origin=AGENT)
    assert [p["path"] for p in e.value.problems] == ["/views/kobu", "/links/l9"]
    assert "kobi" in e.value.problems[0]["fix"] and "l2" in e.value.problems[1]["fix"]


@pytest.mark.parametrize(
    "edits",
    [
        {"views": {"kobi": {"layout": {"position": [330, 0]}}}},
        gallery_edits(),
        {"views": {"kobi": None}, "project": {"label": "renamed"}},
    ],
    ids=["move", "gallery", "remove"],
)
def test_an_edit_and_the_document_it_merges_to_make_the_same_undo_step(edits):
    def fresh() -> LiveCanvas:
        live = LiveCanvas.headless("demo")
        doc = live.document()
        doc["views"]["kobi"] = {
            "type": "gallery",
            "label": "front frames",
            "layout": {"position": [900, 420], "size": [620, 400]},
        }
        pin(doc, "kobi")
        live.submit(doc, origin="user", label="setup")
        return live

    a, b = fresh(), fresh()
    before = a.document()
    by_edit = a.submit(edits=edits, origin=AGENT, label="x")
    by_doc = b.submit(merge_edits(b.data, edits), origin=AGENT, label="x")

    assert by_edit.patch == by_doc.patch and by_edit.renamed == by_doc.renamed
    assert a.history[-1].inverse == b.history[-1].inverse
    assert a.document() == b.document()
    a.undo()
    assert a.document() == before


def test_a_turn_of_edits_and_documents_squashes_and_replays_from_the_journal(tmp_path: Path):
    proj = Project.init(tmp_path / "proj", "demo")
    live = LiveCanvas.open(proj)
    before = live.document()
    start = live.cursor

    added = live.submit(edits=gallery_edits(_tree(live)), origin=AGENT)
    doc = live.document()
    doc["views"][added.renamed["new1"]]["label"] = "picks"
    live.submit(doc, origin=AGENT)
    live.submit(edits={"project": {"label": "review"}}, origin=AGENT)
    after = live.document()
    rec = live.squash(start, label="add a gallery of the picks", origin=AGENT)

    reopened = LiveCanvas.open(proj)
    assert reopened.document() == after and reopened.history_note is None
    assert [r.id for r in reopened.history] == [rec.id]
    reopened.undo()
    assert reopened.document() == before


def _tree(live: LiveCanvas) -> str:
    return next(v for v, d in live.data["views"].items() if d["type"] == "file_tree")
