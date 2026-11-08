"""The turn-start conflict rule (#98, ADR 0013 amendments): a submit made with ``since``
(the prompt bar's turn start, ``LiveCanvas.mark``) merges three ways with what the user
changed after that mark (SPEC-18): per field of each view or link the user touched, the
agent's change and the user's both land; a field both changed is refused, with the
object's live entry. Objects the user did not touch merge as usual. Seam 1: submit to
resolved state."""

from __future__ import annotations

import json

import pytest
from doc_helpers import pin, run_in_thread

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
    assert "layout.position" in problem["fix"] and "leave it out of your edits" in problem["fix"]
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


def test_a_submit_off_the_canvas_s_thread_is_caught(live: LiveCanvas, monkeypatch, capsys):
    """Every submit runs on the thread that owns the canvas (the GUI thread: the prompt
    bar's agent on the qasync loop, the gestures) or on one holding its serial_lock (the
    endpoints), so one submit's read, merge and apply never interleave with another's. A
    change that moved a submit onto another thread is caught: raised under tests, a
    warning in the app."""

    def relabel() -> None:
        live.submit(edits={"views": {"kobi": {"label": "x"}}}, origin=AGENT)

    monkeypatch.setenv("SPECTO_THREAD_CHECK", "raise")
    (error,) = run_in_thread(relabel)
    assert isinstance(error, RuntimeError) and "serial_lock" in str(error)
    assert live.data["views"]["kobi"]["label"] == "kobi"
    monkeypatch.setenv("SPECTO_THREAD_CHECK", "warn")
    assert run_in_thread(relabel) == []
    assert "[WARN]" in capsys.readouterr().err
    assert live.data["views"]["kobi"]["label"] == "x"


def live_entry(problem: dict) -> dict:
    """The live entry a conflict carries ("its entry is now {...}")."""
    return json.loads(problem["message"].split("its entry is now ", 1)[1])


def test_an_edit_that_follows_its_fix_it_lands(live: LiveCanvas):
    """The edit's fix-it: leave the user's field out. Done literally, the next submit lands
    with the rest of the change."""
    since = live.mark()
    gestures.move_views(live, {"kobi": (700, 300)})
    change = {"views": {"kobi": {"layout": {"position": [330, 0]}, "label": "front"}}}
    with pytest.raises(CanvasConflict) as e:
        live.submit(edits=change, origin=AGENT, since=since)
    assert "leave it out of your edits" in e.value.problems[0]["fix"]

    del change["views"]["kobi"]["layout"]  # left out, as the fix-it says
    live.submit(edits=change, origin=AGENT, since=since)
    assert (
        position(live, "kobi") == [700.0, 300.0] and live.data["views"]["kobi"]["label"] == "front"
    )


def test_a_whole_document_that_follows_its_fix_it_lands(live: LiveCanvas):
    """The whole document's fix-it: copy the live value (leaving it out of a document
    would remove it, refused again). Done literally, the next submit lands."""
    since = live.mark()
    doc = live.document()
    gestures.move_views(live, {"kobi": (700, 300)})
    doc["views"]["kobi"]["layout"]["position"] = [330, 0]
    doc["views"]["tamu"]["label"] = "rear"
    with pytest.raises(CanvasConflict) as e:
        live.submit(doc, origin=AGENT, since=since)
    (problem,) = e.value.problems
    assert "copy its live value" in problem["fix"]

    doc["views"]["kobi"]["layout"]["position"] = live_entry(problem)["layout"]["position"]
    live.submit(doc, origin=AGENT, since=since)
    assert (
        position(live, "kobi") == [700.0, 300.0] and live.data["views"]["tamu"]["label"] == "rear"
    )


def test_a_whole_document_that_dropped_a_view_the_user_moved_copies_it_back(live: LiveCanvas):
    since = live.mark()
    doc = live.document()
    gestures.move_views(live, {"kobi": (700, 300)})
    pins = [lid for lid, link in doc["links"].items() if link["target"]["view"] == "kobi"]
    del doc["views"]["kobi"]  # with its pin, as an agent removing it writes
    for lid in pins:
        del doc["links"][lid]
    with pytest.raises(CanvasConflict) as e:
        live.submit(doc, origin=AGENT, since=since)
    problem = next(p for p in e.value.problems if p["path"] == "/views/kobi")
    doc["views"]["kobi"] = live_entry(problem)  # the live value, copied
    doc["links"].update({lid: live.data["links"][lid] for lid in pins})
    live.submit(doc, origin=AGENT, since=since)
    assert position(live, "kobi") == [700.0, 300.0]


def test_a_thread_holding_the_serial_lock_may_submit_and_a_bare_one_may_not(
    live: LiveCanvas, monkeypatch
):
    """The canvas's one serialise lock, which every endpoint takes around a request: a
    submit on a thread holding it passes the thread check; a bare submit from a thread
    still raises, also after a locked one ran (the lock is no switch that stays off)."""
    monkeypatch.setenv("SPECTO_THREAD_CHECK", "raise")

    def locked() -> None:
        with live.serial_lock:
            live.submit(edits={"views": {"kobi": {"label": "locked"}}}, origin=AGENT)

    def bare() -> None:
        live.submit(edits={"views": {"kobi": {"label": "bare"}}}, origin=AGENT)

    assert run_in_thread(locked) == []
    (error,) = run_in_thread(bare)
    assert isinstance(error, RuntimeError)
    assert live.data["views"]["kobi"]["label"] == "locked"


def test_a_submit_holds_the_serial_lock_on_the_owner_thread_too(live: LiveCanvas):
    seen: list[bool] = []
    live.on_patch(lambda patch, meta: seen.append(live.serial_lock.held()))
    live.submit(edits={"views": {"kobi": {"label": "front"}}}, origin=AGENT)
    assert seen == [True] and not live.serial_lock.held()


def test_a_foreign_release_leaves_the_holder_s_lock_alone(live: LiveCanvas):
    lock = live.serial_lock
    with lock:
        (error,) = run_in_thread(lambda: lock.__exit__(None, None, None))
        assert isinstance(error, RuntimeError) and lock.held()
    assert not lock.held()

    def take_and_leave() -> None:
        with lock:
            pass

    assert run_in_thread(take_and_leave) == []


def test_a_closed_view_s_fix_it_names_its_links_and_following_it_lands(live: LiveCanvas):
    """A whole document is validated before the merge: leaving out the view the user
    closed, but not its links, would fail first. The fix-it names both."""
    since = live.mark()
    doc = live.document()
    gestures.close_view(live, "tamu")
    doc["views"]["tamu"]["label"] = "rear"
    doc["views"]["kobi"]["label"] = "front"
    with pytest.raises(CanvasConflict) as e:
        live.submit(doc, origin=AGENT, since=since)
    problem = next(p for p in e.value.problems if p["path"] == "/views/tamu")
    pins = sorted(lid for lid, link in doc["links"].items() if link["target"]["view"] == "tamu")
    assert problem["fix"].startswith(f"leave out tamu and links {', '.join(pins)}")

    del doc["views"]["tamu"]  # as the fix-it says: the view and those links
    for lid in pins:
        del doc["links"][lid]
    live.submit(doc, origin=AGENT, since=since)
    assert "tamu" not in live.data["views"] and live.data["views"]["kobi"]["label"] == "front"


def test_a_user_link_s_fix_it_followed_lands_for_an_edit(live: LiveCanvas):
    since = live.mark()
    lid = user_link(live)
    with pytest.raises(CanvasConflict) as e:
        live.submit(
            edits={"views": {"kobi": None, "tamu": {"label": "rear"}}}, origin=AGENT, since=since
        )
    assert e.value.problems[0]["fix"].startswith("drop the removal of kobi from your edits")
    live.submit(edits={"views": {"tamu": {"label": "rear"}}}, origin=AGENT, since=since)
    assert lid in live.data["links"] and live.data["views"]["tamu"]["label"] == "rear"


def test_a_user_link_s_fix_it_followed_lands_for_a_whole_document(live: LiveCanvas):
    since = live.mark()
    doc = live.document()
    lid = user_link(live)
    del doc["views"]["kobi"]
    doc["links"] = {k: v for k, v in doc["links"].items() if v["target"]["view"] != "kobi"}
    doc["views"]["tamu"]["label"] = "rear"
    with pytest.raises(CanvasConflict) as e:
        live.submit(doc, origin=AGENT, since=since)
    assert "keep kobi and its links in your document" in e.value.problems[0]["fix"]

    doc["views"]["kobi"] = live.data["views"]["kobi"]  # copied from the live canvas
    doc["links"].update(
        {
            k: v
            for k, v in live.data["links"].items()
            if "kobi" in (v["source"]["view"], v["target"]["view"])
        }
    )
    live.submit(doc, origin=AGENT, since=since)
    assert lid in live.data["links"] and live.data["views"]["tamu"]["label"] == "rear"


@pytest.mark.parametrize("form", ["edits", "document"])
def test_a_user_undo_of_the_agent_s_change_is_kept_by_its_next_document(live: LiveCanvas, form):
    """The turn-start copy advances to what the agent last wrote: the agent removes kobi,
    the user undoes that, and the agent's next whole document (still without kobi, as it
    wrote it) keeps the user's undo instead of removing kobi again silently."""
    since = live.mark()
    if form == "edits":
        live.submit(edits={"views": {"kobi": None}}, origin=AGENT, since=since)
    else:
        doc = live.document()
        del doc["views"]["kobi"]
        doc["links"] = {k: v for k, v in doc["links"].items() if v["target"]["view"] != "kobi"}
        live.submit(doc, origin=AGENT, since=since)
    after = live.document()  # what the agent believes now
    assert "kobi" not in after["views"]
    live.undo(origin="user")
    assert "kobi" in live.data["views"]

    after["views"]["tamu"]["label"] = "rear"
    live.submit(after, origin=AGENT, since=since)
    assert "kobi" in live.data["views"] and live.data["views"]["tamu"]["label"] == "rear"


def add_view(live: LiveCanvas, vid: str, label: str, *, origin: str = "user") -> None:
    doc = live.document()
    doc["views"][vid] = {
        "type": "gallery",
        "label": label,
        "layout": {"position": [1800, 0], "size": [600, 400]},
    }
    pin(doc, vid)
    live.submit(doc, origin=origin, label=f"add {vid}")


def test_the_advance_copies_only_the_fields_the_edit_named(live: LiveCanvas):
    """The user adds rena ("a"), the agent reads, the user relabels it ("b"), the agent's
    edit sets rena's params.selected (no conflict: it names another field). The agent's
    copy of rena then holds that field alone, not the whole live entry, so the agent's
    next whole document (its read: "a") meets the user's "b" as a conflict instead of
    reverting it silently."""
    since = live.mark()
    add_view(live, "rena", "a")
    read = live.document()
    live.submit(edits={"views": {"rena": {"label": "b"}}}, origin="user")
    live.submit(
        edits={"views": {"rena": {"params": {"selected": ["x.png"]}}}}, origin=AGENT, since=since
    )
    assert live.data["views"]["rena"]["label"] == "b"

    read["views"]["rena"]["params"] = {"selected": ["x.png"]}
    read["views"]["tamu"]["label"] = "rear"
    with pytest.raises(CanvasConflict) as e:
        live.submit(read, origin=AGENT, since=since)
    (problem,) = e.value.problems
    assert problem["path"] == "/views/rena" and "label" in problem["message"]
    assert live.data["views"]["rena"]["label"] == "b"


def test_the_advance_copies_a_whole_object_the_edit_created(live: LiveCanvas):
    """An object this submit created goes into the copy whole, as the app wrote it (with
    the defaults it filled in), so the agent's next document that repeats it is no change."""
    since = live.mark()
    new = {
        "type": "gallery",
        "label": "Rena",
        "layout": {"position": [1800, 0], "size": [600, 400]},
    }
    pinned = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "new1", "port": "data"},
        "params": {"sources": ["."]},
        "summary": {"sentence": "Rena always shows its sources."},
    }
    res = live.submit(
        edits={"views": {"new1": new}, "links": {"new2": pinned}}, origin=AGENT, since=since
    )
    rena = res.renamed["new1"]
    read = live.document()
    gestures.move_views(live, {rena: (1900, 100)})  # the user drags it

    read["views"]["tamu"]["label"] = "rear"
    live.submit(read, origin=AGENT, since=since)
    assert position(live, rena) == [1900.0, 100.0] and live.data["views"]["tamu"]["label"] == "rear"


def test_the_advance_renames_new_view_ids_inside_the_edit_s_values(live: LiveCanvas):
    """A link the edit retargets onto a new view names it by its placeholder; the copy
    takes the name the app gave it, so the link stays in the copy (instead of being
    dropped as a link whose view is gone) and the agent's next document is judged
    against it."""
    since = live.mark()
    res = live.submit(edits={"links": {"new1": follow("kobi", "tamu")}}, origin=AGENT, since=since)
    lid = res.renamed["new1"]
    new = {
        "type": "gallery",
        "label": "Rena",
        "layout": {"position": [1800, 0], "size": [600, 400]},
    }
    pinned = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "new1", "port": "data"},
        "params": {"sources": ["."]},
        "summary": {"sentence": "Rena always shows its sources."},
    }
    res = live.submit(
        edits={
            "views": {"new1": new},
            "links": {"new2": pinned, lid: {"target": {"view": "new1", "port": "selection"}}},
        },
        origin=AGENT,
        since=since,
    )
    rena = res.renamed["new1"]
    assert live.data["links"][lid]["target"]["view"] == rena
    read = live.document()
    live.submit(edits={"links": {lid: {"params": {"match": "stem"}}}}, origin="user")

    read["views"]["tamu"]["label"] = "rear"
    live.submit(read, origin=AGENT, since=since)  # lid is in the copy as written: no conflict
    assert live.data["links"][lid]["params"]["match"] == "stem"
    assert live.data["views"]["tamu"]["label"] == "rear"


def test_the_agent_s_own_undo_rewinds_its_copy(live: LiveCanvas):
    """The agent removes kobi, then undoes that itself (the undo tool). Its copy goes back
    to what it was before the undone submit, so the user's drag of kobi after the undo
    and the agent's next document (from a read after its undo) both land."""
    since = live.mark()
    live.submit(edits={"views": {"kobi": None}}, origin=AGENT, since=since)
    live.undo(origin=AGENT)
    assert "kobi" in live.data["views"]
    read = live.document()
    gestures.move_views(live, {"kobi": (700, 300)})

    read["views"]["tamu"]["label"] = "rear"
    live.submit(read, origin=AGENT, since=since)
    assert (
        position(live, "kobi") == [700.0, 300.0] and live.data["views"]["tamu"]["label"] == "rear"
    )


def test_undos_out_of_order_leave_no_stale_copy(live: LiveCanvas):
    """The agent relabels kobi (t1) and tamu (t2); the user undoes t2, then the agent
    undoes t1. Its copy must hold neither write (t2's copy was the newest, t1's below
    it), so its fresh relabel of kobi after the user's drag lands instead of meeting its
    own stale label as a conflict."""
    since = live.mark()
    live.submit(edits={"views": {"kobi": {"label": "t1"}}}, origin=AGENT, since=since)
    live.submit(edits={"views": {"tamu": {"label": "t2"}}}, origin=AGENT, since=since)
    live.undo(origin="user")  # t2
    live.undo(origin=AGENT)  # t1
    assert live.data["views"]["kobi"]["label"] == "kobi"
    gestures.move_views(live, {"kobi": (700, 300)})

    live.submit(edits={"views": {"kobi": {"label": "again"}}}, origin=AGENT, since=since)
    assert live.data["views"]["kobi"]["label"] == "again"
    assert position(live, "kobi") == [700.0, 300.0]


def test_a_whole_document_advances_only_the_fields_it_changed(live: LiveCanvas):
    """The user drags tamu; the agent's fresh document (read after the drag) relabels
    kobi and lands. It echoed tamu's new position without changing it, so the copy
    does not absorb it: the agent's next edit moving tamu meets the user's drag as a
    conflict, as it would have without the document in between."""
    since = live.mark()
    gestures.move_views(live, {"tamu": (1200, 500)})
    doc = live.document()
    doc["views"]["kobi"]["label"] = "front"
    live.submit(doc, origin=AGENT, since=since)
    assert live.data["views"]["kobi"]["label"] == "front"

    with pytest.raises(CanvasConflict) as e:
        live.submit(
            edits={"views": {"tamu": {"layout": {"position": [1100, 0]}}}},
            origin=AGENT,
            since=since,
        )
    assert e.value.problems[0]["path"] == "/views/tamu"
    assert position(live, "tamu") == [1200.0, 500.0]


def test_the_agent_s_redo_advances_its_copy_again(live: LiveCanvas):
    since = live.mark()
    live.submit(edits={"views": {"kobi": {"label": "front"}}}, origin=AGENT, since=since)
    live.undo(origin=AGENT)
    live.redo(origin=AGENT)
    read = live.document()  # kobi "front", as the agent wrote it
    live.submit(edits={"views": {"kobi": {"label": "user's"}}}, origin="user")

    read["views"]["tamu"]["label"] = "rear"
    live.submit(read, origin=AGENT, since=since)  # the stale "front" is the agent's own word
    assert live.data["views"]["kobi"]["label"] == "user's"


def test_a_closed_root_view_s_fix_it_says_to_clear_root_view_and_following_it_lands(
    live: LiveCanvas,
):
    doc = live.document()
    doc["views"]["clip"] = {
        "type": "video",
        "label": "clip",
        "layout": {"position": [1800, 0], "size": [600, 400]},
    }
    pin(doc, "clip")
    doc["root_view"] = "clip"
    clip = live.submit(doc, origin="user", label="add clip as root").renamed.get("clip", "clip")
    since = live.mark()
    doc = live.document()
    gestures.close_view(live, clip)
    assert live.data["root_view"] is None
    doc["views"][clip]["label"] = "rear"
    doc["views"]["kobi"]["label"] = "front"
    with pytest.raises(CanvasConflict) as e:
        live.submit(doc, origin=AGENT, since=since)
    problem = next(p for p in e.value.problems if p["path"] == f"/views/{clip}")
    pins = sorted(lid for lid, link in doc["links"].items() if link["target"]["view"] == clip)
    assert problem["fix"].startswith(
        f"leave out {clip} and links {', '.join(pins)} and clear root_view"
    )

    del doc["views"][clip]  # as the fix-it says: the view, those links, the root view
    for lid in pins:
        del doc["links"][lid]
    doc["root_view"] = None
    live.submit(doc, origin=AGENT, since=since)
    assert clip not in live.data["views"] and live.data["views"]["kobi"]["label"] == "front"


def test_without_a_copy_a_closed_view_s_fix_it_names_its_links_too(live: LiveCanvas):
    """``since`` with no copy (0) judges every user change this session; its fix-it for a
    closed view still names the links to leave out with it."""
    doc = live.document()
    gestures.close_view(live, "tamu")
    pins = sorted(lid for lid, link in doc["links"].items() if link["target"]["view"] == "tamu")
    doc["views"]["tamu"]["label"] = "rear"
    with pytest.raises(CanvasConflict) as e:
        live.submit(doc, origin=AGENT, since=0)
    problem = next(p for p in e.value.problems if p["path"] == "/views/tamu")
    assert problem["fix"].startswith(f"leave out tamu and links {', '.join(pins)}")


def test_run_in_thread_fails_on_a_worker_that_does_not_finish():
    import threading

    go = threading.Event()
    try:
        with pytest.raises(AssertionError, match="still running"):
            run_in_thread(go.wait, timeout=0.2)
    finally:
        go.set()
