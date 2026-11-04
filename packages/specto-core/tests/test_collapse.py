"""Seam 1 (#114): collapse a view to its title bar. The state lives in the document
(``layout.collapsed``); collapsing and expanding are user gestures, one submit each, the
agent may set it too, and collapsed views render nothing, so they cost no quota."""

from __future__ import annotations

import pytest

from specto_core import gestures
from specto_core.canvas_example import worked_example
from specto_core.live_canvas import LiveCanvas


@pytest.fixture
def live() -> LiveCanvas:
    """The worked example: tamu (Cam B) at 380, 420, 640 x 380."""
    live = LiveCanvas.headless("demo")
    live.submit(worked_example(), origin="agent:s1", label="compose")
    return live


def test_collapse_keeps_the_size_and_is_one_undo_step(live: LiveCanvas):
    res = gestures.set_collapsed(live, "tamu", True)

    lay = live.document()["views"]["tamu"]["layout"]
    assert lay == {"position": [380.0, 420.0], "size": [640.0, 380.0], "collapsed": True}
    assert res.origin == "user" and res.views["changed"] == ["tamu"]
    assert live.history_summaries()[-1]["label"] == "collapse Cam B"

    gestures.set_collapsed(live, "tamu", False)
    # expanded, the view writes no collapsed key: documents stay as they were
    assert live.document()["views"]["tamu"]["layout"] == {
        "position": [380.0, 420.0],
        "size": [640.0, 380.0],
    }
    assert live.history_summaries()[-1]["label"] == "expand Cam B"

    live.undo()
    assert live.document()["views"]["tamu"]["layout"]["collapsed"] is True
    live.undo()
    assert "collapsed" not in live.document()["views"]["tamu"]["layout"]


AGENT = "agent:s1"


def test_the_agent_may_collapse_and_expand_views_locked_ones_too(live: LiveCanvas):
    """A lock binds moves, resizes, relinks and deletions: collapsing is none of them."""
    gestures.set_locked(live, "tamu", True)
    doc = live.document()
    doc["views"]["tamu"]["layout"]["collapsed"] = True
    doc["views"]["kobi"]["layout"]["collapsed"] = True
    live.submit(doc, origin=AGENT, label="tuck the galleries away")
    views = live.document()["views"]
    assert views["tamu"]["layout"]["collapsed"] is True
    assert views["kobi"]["layout"]["collapsed"] is True

    doc = live.document()
    doc["views"]["tamu"]["layout"]["collapsed"] = False
    live.submit(doc, origin=AGENT, label="show the rear gallery")
    assert "collapsed" not in live.document()["views"]["tamu"]["layout"]


def test_collapsed_views_cost_no_quota():
    from doc_helpers import pin

    from specto_core.canvas_document import CanvasInvalid
    from specto_core.kinds import ViewCost
    from specto_core.quotas import Quotas

    live = LiveCanvas.headless(
        "demo",
        quotas=Quotas(web_views=1),
        view_cost=lambda t: ViewCost(web_views=1) if t in ("json", "markdown") else ViewCost(),
    )
    doc = live.document()
    doc["views"]["kobi"] = {"type": "json", "layout": {"position": [300, 0], "size": [380, 400]}}
    doc["views"]["tamu"] = {
        "type": "markdown",
        "layout": {"position": [700, 0], "size": [380, 400], "collapsed": True},
    }
    pin(doc, "kobi")
    pin(doc, "tamu")
    # two web views inside the window frame, one of them collapsed: within a quota of one
    live.submit(doc, origin=AGENT, label="two web views, one collapsed")

    # expanding it would render a second web view: the other one collapses to make room
    # in the same step (SPEC-34, ADR 0025)
    res = gestures.set_collapsed(live, "tamu", False)
    layouts = {v: live.document()["views"][v]["layout"] for v in ("kobi", "tamu")}
    assert layouts["kobi"].get("collapsed") is True and "collapsed" not in layouts["tamu"]
    assert res.label == "expand tamu, collapse kobi"

    # an agent's submit over the quota is still refused, naming it, with the fix
    doc = live.document()
    doc["views"]["kobi"]["layout"].pop("collapsed")
    with pytest.raises(CanvasInvalid) as e:
        live.submit(doc, origin=AGENT, label="expand both")
    [problem] = e.value.problems
    assert "web_views" in problem["message"] and "tamu" in problem["message"]
    assert "collapse" in problem["fix"]
