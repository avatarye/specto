"""Seam 1 (spec #35 stories 64 and 75 agree): the window frame's origin follows the
user's pan as navigation, not as an undo step, so the quota counts exactly the views
that render. An agent's submit never moves the frame: the window is where the user
put it."""

from __future__ import annotations

from doc_helpers import pin

from specto_core import Engine, Project
from specto_core.canvas_document import CanvasInvalid
from specto_core.live_canvas import LiveCanvas
from specto_core.quotas import Quotas


def test_navigate_moves_the_window_frame_without_an_undo_step():
    live = LiveCanvas.headless("demo")
    seen: list[str] = []
    live.on_patch(lambda patch, meta: seen.append(meta["kind"]))
    live.navigate((2800.0, 40.0))
    rect = live.data["window_frame"]["rect"]
    assert (rect["x"], rect["y"]) == (2800.0, 40.0)
    assert live.history == [] and live.cursor == 0 and seen == ["navigate"]
    assert live.navigate((2800.0, 40.0)) is False  # nothing moved: nothing emitted
    assert seen == ["navigate"]


def test_navigation_survives_a_relaunch_and_the_undo_stack_still_applies(project: Project):
    live = Engine(project=project).live
    doc = live.document()
    doc["views"]["fege"]["label"] = "Data"
    live.submit(doc, label="rename")
    live.navigate((500.0, 0.0))
    again = Engine(project=project).live
    assert again.data["window_frame"]["rect"]["x"] == 500.0
    assert again.cursor == 1 and again.history_note is None
    again.undo()
    assert again.data["views"]["fege"]["label"] != "Data"
    assert again.data["window_frame"]["rect"]["x"] == 500.0


def test_an_agent_submit_keeps_the_frame_where_and_how_big_the_user_has_it():
    live = LiveCanvas.headless("demo")
    stale = live.document()  # read before the user panned and resized the window
    live.navigate((2800.0, 0.0))
    live.submit(
        {**live.document(), "window_frame": {"rect": {"x": 2800.0, "y": 0.0, "w": 1200, "h": 700}}},
        origin="user",
        label="resize window",
    )
    stale["views"]["fege"]["label"] = "Data"
    stale["window_frame"]["rect"]["w"] = 1600  # the agent's stale size: ignored
    live.submit(stale, origin="agent:s1", label="rename")
    assert live.data["window_frame"]["rect"] == {"x": 2800.0, "y": 0.0, "w": 1200.0, "h": 700.0}
    assert live.data["views"]["fege"]["label"] == "Data"


def test_quotas_count_the_views_in_the_panned_frame():
    live = LiveCanvas.headless(
        "demo", quotas=Quotas(web_views=1), view_cost=lambda v: _cost(v["type"])
    )
    doc = live.document()
    for i, (name, x) in enumerate((("kobi", 3000), ("tamu", 3700))):
        doc["views"][name] = {
            "type": "json",
            "label": f"J{i}",
            "layout": {"position": [x, 0], "size": [600, 400]},
        }
        pin(doc, name)
    live.submit(doc, origin="agent:s1", label="two json views far right")  # off screen: free
    live.navigate((2900.0, 0.0))  # the user pans there: now both would render
    doc = live.document()
    doc["views"]["kobi"]["label"] = "J0 renamed"
    try:
        live.submit(doc, origin="agent:s1", label="rename")
    except CanvasInvalid as e:
        assert "web_views=1" in e.problems[0]["message"]
    else:
        raise AssertionError("the quota must count the views inside the panned frame")


def _cost(view_type: str):
    from specto_core.kinds import ViewCost

    return ViewCost(web_views=1) if view_type == "json" else ViewCost()


def test_a_view_remembers_its_selection_without_an_undo_step_and_across_a_relaunch(
    project: Project,
):
    """Spec #35 story 4: selections come back on reopen. A pick is navigation (like a
    pan): it lands in the view's params, journaled, never an undo step."""
    live = Engine(project=project).live
    seen: list[str] = []
    live.on_patch(lambda patch, meta: seen.append(meta["kind"]))
    assert live.remember("fege", {"selected": ["runs/a.jpg"]}) is True
    assert live.data["views"]["fege"]["params"]["selected"] == ["runs/a.jpg"]
    assert live.history == [] and live.cursor == 0 and seen == ["navigate"]
    assert live.remember("fege", {"selected": ["runs/a.jpg"]}) is False  # unchanged: silent
    assert live.remember("lito", {"selected": "x"}) is False  # no such view: nothing
    assert seen == ["navigate"]
    again = Engine(project=project).live
    assert again.data["views"]["fege"]["params"]["selected"] == ["runs/a.jpg"]
    assert again.cursor == 0


def test_an_agent_submit_that_omits_a_remembered_selection_keeps_it():
    """An agent composes from a document that may predate the user's pick (or a canned
    one): what the user picked is theirs, like the window frame. An explicit other value
    still wins."""
    live = LiveCanvas.headless("demo")
    doc = live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [320, 0], "size": [640, 400]},
    }
    pin(doc, "kobi")
    live.submit(doc, origin="agent:s1", label="front gallery")
    live.remember("kobi", {"selected": "front/00004"})
    stale = live.document()
    del stale["views"]["kobi"]["params"]["selected"]
    stale["views"]["kobi"]["label"] = "Cam A"
    live.submit(stale, origin="agent:s1", label="rename")
    assert live.data["views"]["kobi"]["params"]["selected"] == "front/00004"
    assert live.data["views"]["kobi"]["label"] == "Cam A"
    explicit = live.document()
    explicit["views"]["kobi"]["params"]["selected"] = "front/00001"
    live.submit(explicit, origin="agent:s1", label="pick 1")
    assert live.data["views"]["kobi"]["params"]["selected"] == "front/00001"
    gone = live.document()
    del gone["views"]["kobi"]["params"]["selected"]
    live.submit(gone, origin="user", label="the user may drop it")
    assert "selected" not in live.data["views"]["kobi"]["params"]
