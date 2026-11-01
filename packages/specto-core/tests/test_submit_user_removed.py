"""A view or link the user removed mid-turn (#169): the agent's next edit that only adds
a view must go through, and removing the already-removed view again is a no-op.

The depot report: after the user closed koha (and its link l4), every submit that added
one new view was refused with "koha was removed by the user during this turn". The edit
never named koha: the placeholder's fresh name is a hash of the names taken, which were
the same as when koha was handed out, so the new view was named koha again, and the next
link l4 again; the patch then "re-added" what the user removed. Names (and link ids) the
canvas has handed out are never handed out again. Seam 1: submit to resolved state."""

from __future__ import annotations

import pytest
from doc_helpers import pin

from specto_core import gestures
from specto_core.edits import merge_edits
from specto_core.live_canvas import CanvasInvalid, LiveCanvas

AGENT = "agent:prompt"


def new_gallery(label: str, x: float) -> dict:
    return {
        "views": {
            "new1": {
                "type": "gallery",
                "label": label,
                "layout": {"position": [x, 0], "size": [600, 400]},
            }
        },
        "links": {
            "new2": {
                "kind": "intaker",
                "scope": "pinned",
                "source": {"view": "project", "port": "all_files"},
                "target": {"view": "new1", "port": "data"},
                "params": {"sources": ["."]},
                "summary": {"sentence": "Always shows the project."},
            }
        },
    }


@pytest.fixture
def live() -> LiveCanvas:
    live = LiveCanvas.headless("demo")
    doc = live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "kobi",
        "layout": {"position": [400, 0], "size": [600, 400]},
    }
    pin(doc, "kobi")
    live.submit(doc, origin="user", label="setup")
    return live


def test_an_edit_that_only_adds_a_view_goes_through_after_the_user_closed_one(live: LiveCanvas):
    # the agent's earlier turn added a view and a link under placeholders
    first = live.submit(edits=new_gallery("first", 1100), origin=AGENT, label="add")
    name, lid = first.renamed["new1"], first.renamed["new2"]
    # the next turn begins; the user closes that view (its link goes with it)
    since = live.mark()
    gestures.close_view(live, name)
    assert name not in live.data["views"] and lid not in live.data["links"]

    again = live.submit(edits=new_gallery("second", 1100), origin=AGENT, since=since, label="add")

    new_name, new_lid = again.renamed["new1"], again.renamed["new2"]
    assert new_name != name and new_lid != lid  # a removed name is never handed out again
    assert live.data["views"][new_name]["label"] == "second"
    assert name not in live.data["views"]


def test_a_name_from_an_earlier_session_s_history_is_not_reused(live: LiveCanvas):
    """The undo history (restored from the journal across launches) still names the
    removed view: an undo would bring it back, so a new view must not take its name."""
    first = live.submit(edits=new_gallery("first", 1100), origin=AGENT, label="add")
    name = first.renamed["new1"]
    gestures.close_view(live, name)

    again = live.submit(edits=new_gallery("second", 1100), origin=AGENT, label="add")

    assert again.renamed["new1"] != name
    live.undo()  # the second add
    live.undo()  # the close: the first view is back under its name
    assert live.data["views"][name]["label"] == "first"


def test_removing_a_view_the_user_already_removed_is_a_no_op_that_says_so(live: LiveCanvas):
    first = live.submit(edits=new_gallery("first", 1100), origin=AGENT, label="add")
    name = first.renamed["new1"]
    since = live.mark()
    gestures.close_view(live, name)
    depth = len(live.history)

    res = live.submit(edits={"views": {name: None}}, origin=AGENT, since=since, label="remove")

    assert res.empty and res.already_removed == [name]
    assert len(live.history) == depth
    # with something else in the same edit, the rest goes through
    res = live.submit(
        edits={"views": {name: None, "kobi": {"label": "front"}}},
        origin=AGENT,
        since=since,
        label="relabel",
    )
    assert not res.empty and res.already_removed == [name]
    assert live.data["views"]["kobi"]["label"] == "front"


def test_removing_a_name_the_canvas_never_had_is_still_refused(live: LiveCanvas):
    with pytest.raises(CanvasInvalid) as e:
        live.submit(edits={"views": {"kobu": None}}, origin=AGENT)
    assert "no view 'kobu'" in e.value.problems[0]["message"]


def test_merge_edits_skips_a_removal_of_a_retired_id(live: LiveCanvas):
    merged = merge_edits(live.data, {"views": {"zuzo": None}}, retired={"zuzo"})
    assert merged == live.data
