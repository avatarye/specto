"""A submit without a label gets one from its patch (SPEC-18): the agent may leave the
label out of a simple change, and the strip, the turn row and the undo history still say
what changed. Seam 1: submit to resolved state."""

from __future__ import annotations

import pytest
from doc_helpers import pin

from specto_core.diff_text import patch_label
from specto_core.live_canvas import LiveCanvas

AGENT = "agent:prompt"


@pytest.fixture
def live() -> LiveCanvas:
    live = LiveCanvas.headless("demo")
    doc = live.document()
    for vid, x in (("kobi", 400), ("tamu", 1100)):
        doc["views"][vid] = {
            "type": "gallery",
            "label": vid,
            "layout": {"position": [x, 0], "size": [600, 400]},
        }
        pin(doc, vid)
    live.submit(doc, origin="user", label="setup")
    return live


def submitted(live: LiveCanvas, edits: dict) -> str:
    res = live.submit(edits=edits, origin=AGENT)
    assert live.history[-1].label == res.label
    return res.label


def test_a_move_a_resize_and_both(live: LiveCanvas):
    assert submitted(live, {"views": {"kobi": {"layout": {"position": [330, 0]}}}}) == "moved kobi"
    assert submitted(live, {"views": {"kobi": {"layout": {"size": [500, 300]}}}}) == "resized kobi"
    both = {"views": {"tamu": {"layout": {"position": [0, 600], "size": [300, 300]}}}}
    assert submitted(live, both) == "moved and resized tamu"


def test_a_relabel_a_param_and_a_collapse(live: LiveCanvas):
    assert submitted(live, {"views": {"kobi": {"label": "front"}}}) == "relabelled kobi 'front'"
    assert submitted(live, {"views": {"kobi": {"params": {"tile": 160}}}}) == "set kobi tile"
    assert submitted(live, {"views": {"kobi": {"layout": {"collapsed": True}}}}) == (
        "collapsed kobi"
    )
    assert submitted(live, {"views": {"kobi": {"layout": {"collapsed": False}}}}) == (
        "expanded kobi"
    )


def test_a_new_view_with_its_link_is_one_phrase(live: LiveCanvas):
    doc = live.document()
    doc["views"]["new1"] = {
        "type": "image",
        "label": "Rear",
        "layout": {"position": [0, 600], "size": [400, 300]},
    }
    pin(doc, "new1")
    res = live.submit(doc, origin=AGENT)
    name = res.renamed["new1"]
    assert res.label == "added image 'Rear'"
    assert name in live.data["views"]


def test_a_removal_takes_its_links_along(live: LiveCanvas):
    assert submitted(live, {"views": {"tamu": None}}) == "removed tamu"


def test_several_changes_are_listed_up_to_three(live: LiveCanvas):
    edits = {
        "views": {
            "kobi": {"layout": {"position": [0, 600]}},
            "tamu": {"label": "rear"},
        }
    }
    assert submitted(live, edits) == "moved kobi and relabelled tamu 'rear'"
    patch = [
        {"op": "replace", "path": f"/views/v{i}/layout/position", "value": [i, 0]} for i in range(5)
    ]
    assert patch_label(patch) == "moved v0, moved v1 and 3 more changes"


def test_a_given_label_wins_and_an_empty_submit_gets_none(live: LiveCanvas):
    res = live.submit(
        edits={"views": {"kobi": {"layout": {"position": [330, 0]}}}},
        origin=AGENT,
        label="kobi next to the tree",
    )
    assert res.label == "kobi next to the tree"
    assert live.submit(live.document(), origin=AGENT).label == ""
