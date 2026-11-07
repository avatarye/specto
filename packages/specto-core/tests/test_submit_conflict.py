"""The turn-start conflict rule (#98, ADR 0013 amendments): a submit made with ``since``
(the prompt bar's turn start, ``LiveCanvas.mark``) merges three ways with what the user
changed after that mark (SPEC-18): per field of each view or link the user touched, the
agent's change and the user's both land; a field both changed is refused, with the
object's live entry. Objects the user did not touch merge as usual. Seam 1: submit to
resolved state."""

from __future__ import annotations

import pytest
from doc_helpers import pin

from specto_core import gestures
from specto_core.live_canvas import CanvasConflict, LiveCanvas

AGENT = "agent:prompt"


@pytest.fixture
def live() -> LiveCanvas:
    """The file tree and two galleries, kobi and tamu, each pinned from the project."""
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


def position(live: LiveCanvas, vid: str) -> list[float]:
    return live.data["views"][vid]["layout"]["position"]


def test_a_drag_on_another_view_mid_turn_survives_the_agent_s_edit(live: LiveCanvas):
    since = live.mark()
    gestures.move_views(live, {"tamu": (1200, 500)})

    live.submit(
        edits={"views": {"kobi": {"layout": {"position": [330, 0]}}}}, origin=AGENT, since=since
    )

    assert position(live, "kobi") == [330.0, 0.0]
    assert position(live, "tamu") == [1200.0, 500.0]


def test_a_whole_document_read_before_the_drag_keeps_the_drag_and_lands(live: LiveCanvas):
    """SPEC-18: the agent's document still has tamu where the turn began. Its stale copy
    of tamu is no change of the agent's, so the user's drag stays and kobi's move lands
    (it was refused before the three-way merge, and diffed back before #98)."""
    since = live.mark()
    turn_start = live.document()
    gestures.move_views(live, {"tamu": (1200, 500)})
    turn_start["views"]["kobi"]["layout"]["position"] = [330, 0]

    res = live.submit(turn_start, origin=AGENT, since=since)

    assert position(live, "tamu") == [1200.0, 500.0] and position(live, "kobi") == [330.0, 0.0]
    assert res.touched == ["kobi"]


def test_a_whole_document_keeps_a_view_the_user_closed_mid_turn_closed(live: LiveCanvas):
    since = live.mark()
    turn_start = live.document()
    gestures.close_view(live, "tamu")
    turn_start["views"]["kobi"]["label"] = "front"

    live.submit(turn_start, origin=AGENT, since=since)

    assert "tamu" not in live.data["views"] and live.data["views"]["kobi"]["label"] == "front"


def test_a_whole_document_changing_a_view_the_user_closed_is_refused(live: LiveCanvas):
    since = live.mark()
    turn_start = live.document()
    gestures.close_view(live, "tamu")
    turn_start["views"]["tamu"]["label"] = "rear"

    with pytest.raises(CanvasConflict) as e:
        live.submit(turn_start, origin=AGENT, since=since)

    assert [p["path"] for p in e.value.problems] == ["/views/tamu"]
    assert "removed by the user" in e.value.problems[0]["message"]


def test_an_edit_of_another_field_of_the_view_the_user_moved_lands(live: LiveCanvas):
    since = live.mark()
    gestures.move_views(live, {"kobi": (700, 300)})

    live.submit(edits={"views": {"kobi": {"label": "front"}}}, origin=AGENT, since=since)

    assert position(live, "kobi") == [700.0, 300.0]
    assert live.data["views"]["kobi"]["label"] == "front"


def test_an_edit_of_the_view_the_user_changed_mid_turn_is_refused_with_its_live_entry(
    live: LiveCanvas,
):
    since = live.mark()
    gestures.move_views(live, {"kobi": (700, 300)})
    depth = len(live.history)

    with pytest.raises(CanvasConflict) as e:
        live.submit(
            edits={"views": {"kobi": {"layout": {"position": [330, 0]}}}},
            origin=AGENT,
            since=since,
        )

    assert e.value.code == "conflict"
    (problem,) = e.value.problems
    assert problem["path"] == "/views/kobi"
    assert "by the user" in problem["message"] and "[700.0,300.0]" in problem["message"]
    assert "layout.position" in problem["message"]
    # since stays the turn's start: a retry of the same change is refused again, so the
    # fix-it says to keep the user's value or ask, never to write over it
    assert "keep the user's" in problem["fix"] and "ask" in problem["fix"]
    assert "layout.position" in problem["fix"] and "over" not in problem["fix"]
    assert position(live, "kobi") == [700.0, 300.0] and len(live.history) == depth


def test_a_user_undo_mid_turn_is_a_user_change_too(live: LiveCanvas):
    gestures.move_views(live, {"kobi": (700, 300)})
    since = live.mark()
    live.undo(origin="user")

    with pytest.raises(CanvasConflict):
        live.submit(
            edits={"views": {"kobi": {"layout": {"position": [330, 0]}}}},
            origin=AGENT,
            since=since,
        )
    # another field of it is no conflict
    live.submit(edits={"views": {"kobi": {"label": "front"}}}, origin=AGENT, since=since)
    assert position(live, "kobi") == [400.0, 0.0] and live.data["views"]["kobi"]["label"] == "front"


def test_a_since_with_no_turn_start_copy_refuses_as_before(live: LiveCanvas):
    """A ``since`` no ``mark`` returned (an external caller's guess) has no turn-start
    copy to merge against: a change to an object the user touched is refused."""
    gestures.move_views(live, {"kobi": (700, 300)})
    with pytest.raises(CanvasConflict):
        live.submit(edits={"views": {"kobi": {"label": "front"}}}, origin=AGENT, since=0)


def test_changes_before_the_mark_and_the_agent_s_own_are_no_conflict(live: LiveCanvas):
    gestures.move_views(live, {"kobi": (700, 300)})
    since = live.mark()
    live.submit(edits={"views": {"kobi": {"label": "first"}}}, origin=AGENT, since=since)

    live.submit(edits={"views": {"kobi": {"label": "second"}}}, origin=AGENT, since=since)

    assert live.data["views"]["kobi"]["label"] == "second"


def test_without_a_mark_there_is_no_check(live: LiveCanvas):
    """External MCP clients have no turn: their submits are not checked."""
    gestures.move_views(live, {"kobi": (700, 300)})
    live.submit(edits={"views": {"kobi": {"layout": {"position": [330, 0]}}}}, origin=AGENT)
    assert position(live, "kobi") == [330.0, 0.0]


def test_an_edit_setting_a_field_the_user_changed_back_to_its_turn_start_value_conflicts(
    live: LiveCanvas,
):
    """An edit names what it changes: setting kobi's label back to its turn-start value
    is a change of the agent's, not a stale copy, so it meets the user's relabel as a
    conflict instead of being dropped silently."""
    since = live.mark()
    live.submit(edits={"views": {"kobi": {"label": "front"}}}, origin="user")

    with pytest.raises(CanvasConflict) as e:
        live.submit(edits={"views": {"kobi": {"label": "kobi"}}}, origin=AGENT, since=since)

    assert "label" in e.value.problems[0]["message"]
    assert live.data["views"]["kobi"]["label"] == "front"


def follow(src: str, dst: str) -> dict:
    return {
        "kind": "follow",
        "source": {"view": src, "port": "selection"},
        "target": {"view": dst, "port": "selection"},
        "two_way": True,
        "params": {"match": None},
        "summary": {"sentence": f"{dst} follows the pick in {src}."},
    }


def user_link(live: LiveCanvas) -> str:
    res = live.submit(edits={"links": {"new1": follow("kobi", "tamu")}}, origin="user")
    return res.renamed["new1"]


def test_removing_a_view_the_user_linked_mid_turn_is_a_conflict_on_that_link(live: LiveCanvas):
    since = live.mark()
    lid = user_link(live)

    with pytest.raises(CanvasConflict) as e:
        live.submit(edits={"views": {"kobi": None}}, origin=AGENT, since=since)

    (problem,) = e.value.problems
    assert problem["path"] == f"/links/{lid}" and "kobi" in problem["message"]
    assert "kobi" in live.data["views"] and lid in live.data["links"]


def test_a_stale_whole_document_without_a_view_the_user_linked_is_a_conflict_too(
    live: LiveCanvas,
):
    since = live.mark()
    turn_start = live.document()
    lid = user_link(live)
    del turn_start["views"]["kobi"]
    turn_start["links"] = {
        k: v for k, v in turn_start["links"].items() if v["target"]["view"] != "kobi"
    }

    with pytest.raises(CanvasConflict) as e:
        live.submit(turn_start, origin=AGENT, since=since)

    assert [p["path"] for p in e.value.problems] == [f"/links/{lid}"]


def test_a_stale_whole_document_keeps_a_link_the_user_added(live: LiveCanvas):
    since = live.mark()
    turn_start = live.document()
    lid = user_link(live)
    turn_start["views"]["tamu"]["label"] = "rear"

    live.submit(turn_start, origin=AGENT, since=since)

    assert lid in live.data["links"] and live.data["views"]["tamu"]["label"] == "rear"


def test_each_mark_keeps_its_own_turn_start_copy(live: LiveCanvas):
    first = live.mark()
    second = live.mark()
    assert first != second  # two marks with no change between are still two turn starts
    gestures.move_views(live, {"kobi": (700, 300)})
    live.submit(edits={"views": {"kobi": {"label": "front"}}}, origin=AGENT, since=first)
    assert position(live, "kobi") == [700.0, 300.0]
