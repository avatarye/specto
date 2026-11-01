"""Seam 1 (#48): locks and quotas are enforced when a canvas document is submitted. A
locked view refuses the agent's moves, resizes, deletions, unlocks and relinks with a
fix-it that names the lock holder; the user's own gestures pass. Quotas on costly views
inside the window frame are refused with the offending views named."""

from __future__ import annotations

import pytest
from doc_helpers import pin

from specto_core.canvas_document import CanvasInvalid
from specto_core.live_canvas import LiveCanvas

AGENT = "agent:s1"


def with_locked_gallery() -> LiveCanvas:
    """A canvas whose user locked the gallery v2 (fed by the file tree's picks)."""
    live = LiveCanvas.headless("demo")
    doc = live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [300, 0], "size": [600, 400]},
        "locked": True,
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "selection",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["runs/front"]},
        "summary": {"sentence": "The gallery shows the frames picked in the file tree."},
    }
    live.submit(doc, origin="user", label="lock the gallery")
    return live


def test_a_locked_view_refuses_an_agent_move_with_the_unlock_fixit():
    live = with_locked_gallery()
    before = live.document()
    moved = live.document()
    moved["views"]["kobi"]["layout"]["position"] = [640, 120]

    with pytest.raises(CanvasInvalid) as e:
        live.submit(moved, origin=AGENT, label="move the gallery")

    [problem] = e.value.problems
    assert problem["path"] == "/views/kobi/layout"
    assert "locked by the user" in problem["message"] and "move" in problem["message"]
    assert "ask the user to unlock" in problem["fix"] and "kobi" in problem["fix"]
    assert live.document() == before and live.undo_info()["depth"] == 1

    # the user's own gesture is not refused: the lock is theirs
    res = live.submit(moved, origin="user", label="move the gallery")
    assert res.views["changed"] == ["kobi"]


def test_delete_unlock_and_relink_of_a_locked_view_come_back_together():
    live = with_locked_gallery()
    doc = live.document()
    doc["views"]["kobi"]["locked"] = False  # the agent takes the lock off ...
    doc["links"]["l2"]["source"] = {"view": "project", "port": "all_files"}  # ... and rewires it
    doc["links"]["l2"]["scope"] = "pinned"
    with pytest.raises(CanvasInvalid) as e:
        live.submit(doc, origin=AGENT, label="unlock and rewire")
    assert [p["path"] for p in e.value.problems] == ["/views/kobi/locked", "/links/l2"]
    assert "relinked" in e.value.problems[1]["message"]

    gone = live.document()
    del gone["views"]["kobi"]
    del gone["links"]["l2"]
    with pytest.raises(CanvasInvalid) as e:
        live.submit(gone, origin=AGENT, label="close the gallery")
    # one problem: the link leaving with its view is a consequence, not a second offence
    assert [p["path"] for p in e.value.problems] == ["/views/kobi"]
    assert "cannot be deleted" in e.value.problems[0]["message"]

    # what a lock does not cover: the label, the params, and locking a view (never unlocking)
    doc = live.document()
    doc["views"]["kobi"]["label"] = "Front frames"
    doc["views"]["kobi"]["params"] = {"columns": 4}
    doc["views"]["fege"]["locked"] = True
    assert live.submit(doc, origin=AGENT, label="rename, 4 columns, lock the tree").views[
        "changed"
    ] == ["fege", "kobi"]


# -- quotas ------------------------------------------------------------------------------
def test_a_quota_overrun_inside_the_window_frame_is_refused_with_the_offenders_named():
    from specto_core.kinds import ViewCost
    from specto_core.quotas import Quotas

    live = LiveCanvas.headless(
        "demo",
        quotas=Quotas(web_views=2),
        view_cost=lambda t: (
            ViewCost(web_views=1) if t in ("json", "markdown", "map") else ViewCost()
        ),
    )
    doc = live.document()
    for name, vtype, x in [("kobi", "json", 300), ("tamu", "markdown", 700), ("rena", "map", 1100)]:
        doc["views"][name] = {"type": vtype, "layout": {"position": [x, 0], "size": [380, 400]}}
        pin(doc, name)
    with pytest.raises(CanvasInvalid) as e:
        live.submit(doc, origin=AGENT, label="three web views")
    [problem] = e.value.problems
    assert problem["path"] == "/views"
    assert "web_views" in problem["message"] and "2" in problem["message"]
    assert "kobi" in problem["fix"] and "rena" in problem["fix"]
    assert "window frame" in problem["fix"]

    # off-screen views are paused and cost nothing: the third one outside the frame is fine
    doc["views"]["rena"]["layout"]["position"] = [1700, 0]
    assert set(live.submit(doc, origin=AGENT, label="map parked").views["added"]) == {
        "kobi",
        "tamu",
        "rena",
    }
    # the user is not exempt from a quota: the GPU is finite whoever moves the view
    doc["views"]["rena"]["layout"]["position"] = [1100, 0]
    with pytest.raises(CanvasInvalid):
        live.submit(doc, origin="user", label="bring the map back")
