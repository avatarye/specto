"""Seam 1 (#112, ADR 0021): the float layer. Float and Unfloat are user gestures, one
submit each; a floating view keeps its offset from the nearest window corner, so it
stays put when the window resizes; Unfloat drops it on the canvas point under it. The
agent may float and unfloat views but not move a view the user floated."""

from __future__ import annotations

import pytest

from specto_core import gestures
from specto_core.canvas_document import CanvasInvalid
from specto_core.canvas_example import worked_example
from specto_core.errors import InvalidArgs
from specto_core.float_layer import COLLAPSED_H, view_window_position
from specto_core.live_canvas import LiveCanvas


@pytest.fixture
def live() -> LiveCanvas:
    """The worked example in a 1600 x 900 window frame at the canvas origin: tamu (Rear
    camera) at 380, 420, 640 x 380 sits in its lower-left quarter; vesa (Picked image)
    at 1700, 0 lies past its right edge."""
    live = LiveCanvas.headless("demo")
    live.submit(worked_example(), origin="agent:s1", label="compose")
    return live


def test_float_keeps_the_view_where_it_is_anchored_to_the_nearest_window_corner(
    live: LiveCanvas,
):
    res = gestures.float_view(live, "tamu")

    lay = live.document()["views"]["tamu"]["layout"]
    assert lay["floating"] == {"corner": "bottom_left", "offset": [380.0, 100.0], "by": "user"}
    assert res.origin == "user" and res.views["changed"] == ["tamu"]
    assert live.history_summaries()[-1]["label"] == "float Cam B"
    live.undo()
    assert "floating" not in live.document()["views"]["tamu"]["layout"]


def test_a_view_floated_from_past_the_window_edge_is_pulled_inside(live: LiveCanvas):
    gestures.float_view(live, "vesa")

    lay = live.document()["views"]["vesa"]["layout"]
    assert lay["floating"]["corner"] == "top_right" and lay["floating"]["offset"] == [0.0, 0.0]


def test_a_floating_view_keeps_its_corner_offset_when_the_window_resizes(live: LiveCanvas):
    gestures.float_view(live, "tamu")
    doc = live.document()
    assert view_window_position(doc, doc["views"]["tamu"]) == (380.0, 420.0)

    gestures.set_window_frame(live, (1200.0, 700.0))

    doc = live.document()
    # 380 px from the left edge and 100 px above the bottom edge, as before
    assert view_window_position(doc, doc["views"]["tamu"]) == (380.0, 220.0)
    assert doc["views"]["tamu"]["layout"]["floating"]["offset"] == [380.0, 100.0]

    # a window too small for the offset keeps the view inside it
    gestures.set_window_frame(live, (900.0, 420.0))
    doc = live.document()
    assert view_window_position(doc, doc["views"]["tamu"]) == (260.0, 0.0)


def test_unfloat_drops_the_view_on_the_canvas_point_under_it(live: LiveCanvas):
    gestures.float_view(live, "tamu")
    live.navigate((2000.0, 500.0))  # the user panned away: the floater stayed in the window
    gestures.set_window_frame(live, (1200.0, 700.0))  # and narrowed the window

    res = gestures.unfloat_view(live, "tamu")

    lay = live.document()["views"]["tamu"]["layout"]
    # window position (380, 220) at the panned window origin (2000, 500)
    assert lay == {"position": [2380.0, 720.0], "size": [640.0, 380.0]}
    assert res.origin == "user" and live.history_summaries()[-1]["label"] == "unfloat Cam B"
    with pytest.raises(InvalidArgs):
        gestures.unfloat_view(live, "tamu")


def test_dragging_a_floating_view_reanchors_it_to_its_new_nearest_corner(live: LiveCanvas):
    gestures.float_view(live, "tamu")

    gestures.place_floating(live, "tamu", (900.0, 30.0))
    lay = live.document()["views"]["tamu"]["layout"]
    assert lay["floating"] == {"corner": "top_right", "offset": [60.0, 30.0], "by": "user"}
    assert live.history_summaries()[-1]["label"] == "move Cam B"

    # a border drag resizes it in the same frame of reference
    gestures.place_floating(live, "tamu", (1000.0, 30.0), size=(500.0, 300.0))
    lay = live.document()["views"]["tamu"]["layout"]
    assert lay["size"] == [500.0, 300.0]
    assert lay["floating"] == {"corner": "top_right", "offset": [100.0, 30.0], "by": "user"}
    assert live.history_summaries()[-1]["label"] == "resize Cam B"


def test_a_collapsed_floater_is_anchored_and_kept_inside_by_its_title_bar(live: LiveCanvas):
    """#116: a collapsed view floats as its title bar (COLLAPSED_H tall), so a bar near
    the window's bottom edge stays where it is dropped instead of jumping up by its
    stored height."""
    gestures.move_view(live, "tamu", (380.0, 860.0))  # the bar's bottom at 896 of 900
    gestures.set_collapsed(live, "tamu", True)
    assert COLLAPSED_H == 36.0

    gestures.float_view(live, "tamu")
    doc = live.document()
    assert doc["views"]["tamu"]["layout"]["floating"]["offset"] == [380.0, 4.0]
    assert view_window_position(doc, doc["views"]["tamu"]) == (380.0, 860.0)

    gestures.place_floating(live, "tamu", (700.0, 850.0))
    doc = live.document()
    assert doc["views"]["tamu"]["layout"]["floating"] == {
        "corner": "bottom_right",
        "offset": [260.0, 14.0],
        "by": "user",
    }
    assert view_window_position(doc, doc["views"]["tamu"]) == (700.0, 850.0)

    gestures.unfloat_view(live, "tamu")
    lay = live.document()["views"]["tamu"]["layout"]
    assert lay["position"] == [700.0, 850.0] and lay["size"] == [640.0, 380.0]


AGENT = "agent:s1"


def test_the_agent_may_float_and_unfloat_views_in_a_submit(live: LiveCanvas):
    doc = live.document()
    doc["views"]["kobi"]["layout"]["floating"] = {"corner": "top_right", "offset": [16, 16]}
    live.submit(doc, origin=AGENT, label="float the front gallery")
    floating = live.document()["views"]["kobi"]["layout"]["floating"]
    assert floating == {"corner": "top_right", "offset": [16.0, 16.0], "by": "agent"}

    # a view the user floated: the agent may still unfloat it
    gestures.float_view(live, "tamu")
    doc = live.document()
    del doc["views"]["tamu"]["layout"]["floating"]
    doc["views"]["tamu"]["layout"]["position"] = [380, 900]
    live.submit(doc, origin=AGENT, label="put the rear gallery back")
    assert live.document()["views"]["tamu"]["layout"] == {
        "position": [380.0, 900.0],
        "size": [640.0, 380.0],
    }


def test_the_agent_may_not_move_or_resize_a_view_the_user_floated(live: LiveCanvas):
    gestures.float_view(live, "tamu")
    before = live.document()
    moved = live.document()
    moved["views"]["tamu"]["layout"]["floating"]["corner"] = "top_right"
    moved["views"]["tamu"]["layout"]["size"] = [300, 200]

    with pytest.raises(CanvasInvalid) as e:
        live.submit(moved, origin=AGENT, label="tuck the rear gallery away")

    [problem] = e.value.problems
    assert problem["path"] == "/views/tamu/layout"
    assert "floated by the user" in problem["message"]
    assert "unfloat" in problem["fix"] and "corner bottom_left" in problem["fix"]
    assert live.document() == before
    # the user's own drag is not refused
    live.submit(moved, origin="user", label="tuck the rear gallery away")


def test_only_the_users_float_gesture_marks_a_view_as_the_users(live: LiveCanvas):
    doc = live.document()
    doc["views"]["kobi"]["layout"]["floating"] = {
        "corner": "top_left",
        "offset": [0, 0],
        "by": "user",
    }
    with pytest.raises(CanvasInvalid) as e:
        live.submit(doc, origin=AGENT, label="float the front gallery")
    [problem] = e.value.problems
    assert problem["path"] == "/views/kobi/layout/floating/by"
    assert "by: agent" in problem["fix"]

    # nor may the agent take a user's float over as its own
    gestures.float_view(live, "tamu")
    doc = live.document()
    doc["views"]["tamu"]["layout"]["floating"]["by"] = "agent"
    with pytest.raises(CanvasInvalid) as e:
        live.submit(doc, origin=AGENT, label="claim the rear gallery")
    [problem] = e.value.problems
    assert problem["path"] == "/views/tamu/layout/floating/by"
    assert "keep by: user" in problem["fix"]


def test_a_view_the_agent_floated_stays_the_agents_to_move(live: LiveCanvas):
    doc = live.document()
    doc["views"]["kobi"]["layout"]["floating"] = {"corner": "top_right", "offset": [16, 16]}
    live.submit(doc, origin=AGENT, label="float the front gallery")
    doc = live.document()
    doc["views"]["kobi"]["layout"]["floating"]["offset"] = [40, 40]
    live.submit(doc, origin=AGENT, label="nudge it")
    assert live.document()["views"]["kobi"]["layout"]["floating"]["offset"] == [40.0, 40.0]


def test_floating_views_count_against_the_quota_wherever_the_canvas_pans():
    from doc_helpers import pin

    from specto_core.kinds import ViewCost
    from specto_core.quotas import Quotas

    live = LiveCanvas.headless(
        "demo",
        quotas=Quotas(web_views=1),
        view_cost=lambda v: (
            ViewCost(web_views=1) if v["type"] in ("json", "markdown") else ViewCost()
        ),
    )
    doc = live.document()
    doc["views"]["kobi"] = {
        "type": "json",
        "layout": {"position": [300, 0], "size": [380, 400]},
    }
    doc["views"]["tamu"] = {
        "type": "markdown",
        "layout": {"position": [5000, 0], "size": [380, 400]},  # off-screen: free
    }
    pin(doc, "kobi")
    pin(doc, "tamu")
    live.submit(doc, origin=AGENT, label="two web views, one on screen")
    live.navigate((4000.0, 0.0))  # kobi is off-screen now, tamu inside the frame
    gestures.float_view(live, "tamu")
    live.navigate((0.0, 0.0))  # back: kobi on screen again, tamu still floats there

    doc = live.document()
    doc["views"]["kobi"]["label"] = "Metadata"
    with pytest.raises(CanvasInvalid) as e:
        live.submit(doc, origin=AGENT, label="rename")
    [problem] = e.value.problems
    assert "web_views" in problem["message"] and "tamu" in problem["message"]
    assert "unfloat" in problem["fix"]
