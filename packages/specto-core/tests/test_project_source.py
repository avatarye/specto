"""Seam 1 (#83): the project source is the one source of all files. It has one output,
``all_files``; the file tree keeps only ``selected_files``; every view must be reachable
from the project source by links; a saved canvas wired off the file tree's ``all_files``
(or the old ``project.folder``) is rewired on load, journaled once, no undo step."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from specto_core import Project
from specto_core.canvas_document import (
    CanvasInvalid,
    new_canvas_document,
    rewire_project_links,
    validate_canvas,
)
from specto_core.canvas_example import worked_example
from specto_core.live_canvas import LiveCanvas
from specto_core.ports import PROJECT_PORTS, VIEW_PORTS


def problems_of(data: dict) -> dict[str, dict]:
    with pytest.raises(CanvasInvalid) as e:
        validate_canvas(data)
    print(e.value)
    assert all(p["fix"] for p in e.value.problems), "every problem carries a fix-it"
    return {p["path"]: p for p in e.value.problems}


PROJECT_SRC = {"view": "project", "port": "all_files"}


def gallery(doc: dict, vid: str = "kobi") -> dict:
    doc["views"][vid] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [380, 0], "size": [640, 400]},
    }
    return doc


def pinned(doc: dict, lid: str, source: dict, target: str) -> dict:
    doc["links"][lid] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": source,
        "target": {"view": target, "port": "data"},
        "params": {"sources": ["runs/front"], "screener": ["@images"]},
        "summary": {"sentence": "The gallery always shows the front run."},
    }
    return doc


# -- ports -------------------------------------------------------------------------------
def test_the_project_source_outputs_all_files_and_the_file_tree_only_selected_files():
    assert dict(PROJECT_PORTS.outputs) == {"all_files": "files"}
    assert dict(VIEW_PORTS["file_tree"].outputs) == {"selected_files": "files"}
    doc = validate_canvas(worked_example())
    assert doc.links["l1"].source.view == "project" and doc.links["l1"].source.port == "all_files"
    # the pinned links of the example hang off the project source, not the file tree
    assert all(
        ln.source.view == "project"
        for ln in doc.links.values()
        if ln.kind == "intaker" and ln.scope == "pinned"
    )
    # a fresh canvas leaves room for the project node left of the file tree
    fresh = new_canvas_document("demo")
    assert fresh["views"]["fege"]["layout"]["position"][0] > 0
    assert fresh["links"]["l1"]["source"] == {"view": "project", "port": "all_files"}
    # and its file tree is as tall as the window frame, as the example's fits in its (#125)
    assert fresh["views"]["fege"]["layout"]["size"][1] == fresh["window_frame"]["rect"]["h"]
    (_, y), (_, h) = doc.views["fege"].layout.position, doc.views["fege"].layout.size
    assert y + h <= doc.window_frame.rect.h


def test_links_off_the_file_trees_all_files_or_project_folder_are_refused_with_the_way_out():
    tree_all = {"view": "fege", "port": "all_files"}  # the port the file tree no longer has
    doc = pinned(gallery(new_canvas_document()), "l2", tree_all, "kobi")
    p = problems_of(doc)
    assert "all_files" in p["/links/l2/source"]["message"]
    assert "project.all_files" in p["/links/l2/source"]["fix"]

    doc = new_canvas_document()
    doc["links"]["l1"]["source"] = {"view": "project", "port": "folder"}  # the old port
    p = problems_of(doc)
    assert (
        "folder" in p["/links/l1/source"]["message"] and "all_files" in p["/links/l1/source"]["fix"]
    )


def test_the_project_source_is_not_a_view():
    doc = new_canvas_document()
    doc["views"]["project"] = {"type": "gallery", "label": "nope"}
    p = problems_of(doc)
    assert "project source" in p["/views/project"]["fix"]


# -- reachability: binds the agent, like a lock (user decision on #83) ----------------------
def agent_refusal(doc: dict, live: LiveCanvas | None = None) -> dict[str, dict]:
    """Submit ``doc`` as the agent; it must be refused. Problems keyed by path."""
    live = live or LiveCanvas.headless("demo")
    with pytest.raises(CanvasInvalid) as e:
        live.submit(doc, origin="agent:s1")
    print(e.value)
    assert all(p["fix"] for p in e.value.problems), "every problem carries a fix-it"
    return {p["path"]: p for p in e.value.problems}


def test_an_agent_submit_with_a_view_unreachable_from_the_project_source_is_refused():
    doc = gallery(new_canvas_document())
    p = agent_refusal(doc)
    prob = p["/views/kobi"]
    assert "kobi" in prob["message"] and "project source" in prob["message"]
    # the suggestion is a concrete link: an intaker from the file tree's picks into kobi.data
    assert (
        "intaker" in prob["fix"]
        and "fege.selected_files" in prob["fix"]
        and "kobi.data" in prob["fix"]
    )

    # a map takes content on features: read_contents from the project source
    doc = new_canvas_document()
    doc["views"]["pola"] = {"type": "map", "label": "Trajectory"}
    prob = agent_refusal(doc)["/views/pola"]
    assert (
        "read_contents" in prob["fix"]
        and "project.all_files" in prob["fix"]
        and "pola.features" in prob["fix"]
    )


def test_reachability_follows_links_including_two_way_ones_and_names_the_broken_end():
    doc = gallery(gallery(new_canvas_document()), "tamu")
    pinned(doc, "l2", {"view": "project", "port": "all_files"}, "kobi")
    doc["links"]["l3"] = {
        "kind": "follow",
        "two_way": True,
        "source": {"view": "tamu", "port": "selection"},
        "target": {"view": "kobi", "port": "selection"},
        "params": {"match": "frame"},
        "summary": {"sentence": "The two galleries select the same frame."},
    }
    # tamu reaches kobi through the two-way link, and kobi reaches the project: fine
    live = LiveCanvas.headless("demo")
    live.submit(doc, origin="agent:s1")

    # without a path to the project source, a chain of views is refused at its broken end
    del doc["links"]["l2"]
    doc["links"]["l3"]["two_way"] = False
    p = agent_refusal(doc, live)
    assert set(p) == {"/views/tamu", "/views/kobi"}
    assert "tamu" in p["/views/kobi"]["fix"]  # kobi follows tamu: make tamu reachable

    # the file tree itself needs its intaker from the project source
    doc = new_canvas_document()
    del doc["links"]["l1"]
    prob = agent_refusal(doc)["/views/fege"]
    assert "project.all_files" in prob["fix"] and "fege.data" in prob["fix"]


def test_the_user_may_orphan_a_view_and_the_agent_is_not_blamed_for_it():
    """User gestures are not bound by reachability (like locks): deleting a view's only
    link leaves it "not connected". A later agent submit that leaves that view as it is
    goes through; one that orphans another view, or adds an unconnected one, does not."""
    from specto_core import gestures
    from specto_core.canvas_document import unreachable_views

    live = LiveCanvas.headless("demo")
    doc = pinned(gallery(gallery(new_canvas_document()), "tamu"), "l2", PROJECT_SRC, "kobi")
    pinned(doc, "l3", PROJECT_SRC, "tamu")
    live.submit(doc, origin="agent:s1")

    gestures.delete_link(live, "l2")  # the user: allowed, kobi is not connected now
    assert "l2" not in live.document()["links"]
    assert unreachable_views(live.document()) == {"kobi"}
    validate_canvas(live.document())  # plain validation does not judge reachability

    # the agent relabels tamu: kobi stays unconnected, the submit is not refused for it
    doc = live.document()
    doc["views"]["tamu"]["label"] = "Rear"
    assert live.submit(doc, origin="agent:s1").views["changed"] == ["tamu"]
    # the agent removes tamu's link: refused, tamu was connected
    doc = live.document()
    del doc["links"]["l3"]
    assert set(agent_refusal(doc, live)) == {"/views/tamu"}
    # the agent adds a view with no link: refused
    doc = gallery(live.document(), "rena")
    assert set(agent_refusal(doc, live)) == {"/views/rena"}
    # the agent relinks kobi: fine, and nothing is unconnected any more
    doc = pinned(live.document(), "l4", PROJECT_SRC, "kobi")
    live.submit(doc, origin="agent:s1")
    assert unreachable_views(live.document()) == set()


def test_a_pinned_intaker_hangs_off_the_project_source_and_a_selection_one_off_the_tree():
    # pinned from the project source needs no more; selection from the project is wrong
    doc = pinned(
        gallery(new_canvas_document()), "l2", {"view": "project", "port": "all_files"}, "kobi"
    )
    validate_canvas(doc)
    doc["links"]["l2"]["scope"] = "selection"
    assert "selected_files" in problems_of(doc)["/links/l2/scope"]["fix"]
    # an intaker from the project into a view other than the file tree says it is pinned
    del doc["links"]["l2"]["scope"]
    assert "pinned" in problems_of(doc)["/links/l2/scope"]["fix"]
    # pinned off the tree's selected_files is the wrong pairing
    doc["links"]["l2"]["scope"] = "pinned"
    doc["links"]["l2"]["source"] = {"view": "fege", "port": "selected_files"}
    prob = problems_of(doc)["/links/l2/source"]
    assert "project.all_files" in prob["fix"] and "selection" in prob["fix"]


# -- load: links move to the project source ---------------------------------------------------
def legacy_wiring(doc: dict) -> dict:
    """A canvas saved before #83: the project intaker off ``folder``, pinned links and a
    read_contents off the file tree's ``all_files``."""
    for ln in doc["links"].values():
        if ln["source"] == {"view": "project", "port": "all_files"}:
            ln["source"] = {"view": "fege", "port": "all_files"}
    doc["links"]["l1"]["source"] = {"view": "project", "port": "folder"}
    return doc


def test_rewire_project_links_moves_file_tree_all_files_and_project_folder_to_the_source():
    doc = legacy_wiring(worked_example())
    moved = rewire_project_links(doc)
    assert moved == ["l1", "l2", "l3", "l4", "l5", "l6"]
    assert doc == worked_example()
    assert rewire_project_links(doc) == []  # nothing left to move


def test_a_canvas_wired_off_the_file_tree_is_rewired_once_on_open_with_one_journal_entry(
    tmp_path: Path,
):
    project = Project.init(tmp_path / "proj", "demo")
    project.canvas_file.write_text(json.dumps(legacy_wiring(worked_example())), encoding="utf-8")

    live = LiveCanvas.open(project)

    example = validate_canvas(worked_example()).to_data()
    assert live.document() == example
    assert live.rewired == ["l1", "l2", "l3", "l4", "l5", "l6"]
    assert live.undo_info()["depth"] == 0  # not an undo step
    entries = live.journal.read()
    assert [e.kind for e in entries] == ["rewire"] and entries[0].rewired == live.rewired
    assert entries[0].patch and entries[0].inverse
    saved = json.loads(project.canvas_file.read_text(encoding="utf-8"))
    assert saved["links"] == example["links"]

    # the next open finds the links on the project source and rewires nothing
    again = LiveCanvas.open(project)
    assert again.rewired == [] and [e.kind for e in again.journal.read()] == ["rewire"]
    assert again.document() == live.document()


def test_a_headless_canvas_built_from_old_wiring_loads_too():
    live = LiveCanvas(legacy_wiring(worked_example()), journal=False, autosave=False)
    assert live.rewired == ["l1", "l2", "l3", "l4", "l5", "l6"]
    assert live.document()["links"]["l2"]["source"] == {"view": "project", "port": "all_files"}


# -- the node's spot at the canvas origin stays free ------------------------------------------
def test_a_saved_canvas_with_its_file_tree_at_the_origin_moves_right_on_open(tmp_path: Path):
    """A canvas saved before #83 has the file tree at x = 0, under the node: the whole
    layout moves right by the same step, in the same rewrite entry."""
    project = Project.init(tmp_path / "proj", "demo")
    old = legacy_wiring(worked_example())
    for v in old["views"].values():
        v["layout"]["position"][0] -= 80
    project.canvas_file.write_text(json.dumps(old), encoding="utf-8")

    live = LiveCanvas.open(project)

    assert live.document() == validate_canvas(worked_example()).to_data()
    (entry,) = live.journal.read()
    assert entry.kind == "rewire" and "moved the layout" in entry.label
    assert live.undo_info()["depth"] == 0


def test_a_view_keyed_project_is_refused_on_submit_and_never_renamed():
    """``project`` is the project source's fixed id: a submit does not take it for a
    placeholder (which would also rewrite the links off the project source)."""
    doc = new_canvas_document()
    doc["views"]["project"] = {"type": "gallery", "label": "nope"}
    p = agent_refusal(doc)
    assert "project source" in p["/views/project"]["fix"]
    assert "/links/l1/source" not in p
