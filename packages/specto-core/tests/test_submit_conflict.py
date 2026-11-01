"""The turn-start conflict rule (#98, ADR 0013 amendment): a submit made with ``since``
(the prompt bar's turn start, ``LiveCanvas.mark``) is refused when it would change an
object the user changed after that mark; the refusal carries the object's live entry.
Objects the user did not touch merge as usual. Seam 1: submit to resolved state."""

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


def test_a_whole_document_read_before_the_drag_no_longer_moves_the_view_back(
    live: LiveCanvas,
):
    """Today's latent revert: the agent's document still has tamu where the turn began.
    It is refused rather than diffed back over the user's drag."""
    since = live.mark()
    turn_start = live.document()
    gestures.move_views(live, {"tamu": (1200, 500)})
    turn_start["views"]["kobi"]["layout"]["position"] = [330, 0]

    with pytest.raises(CanvasConflict) as e:
        live.submit(turn_start, origin=AGENT, since=since)

    assert position(live, "tamu") == [1200.0, 500.0] and position(live, "kobi") == [400.0, 0.0]
    assert [p["path"] for p in e.value.problems] == ["/views/tamu"]


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
    assert "again" in problem["fix"]
    assert position(live, "kobi") == [700.0, 300.0] and len(live.history) == depth


def test_a_user_undo_mid_turn_is_a_user_change_too(live: LiveCanvas):
    gestures.move_views(live, {"kobi": (700, 300)})
    since = live.mark()
    live.undo(origin="user")

    with pytest.raises(CanvasConflict):
        live.submit(edits={"views": {"kobi": {"label": "front"}}}, origin=AGENT, since=since)


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
