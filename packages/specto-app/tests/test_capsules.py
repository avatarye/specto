"""Port capsules (#160, glossary: port capsule): the two ports a link joins show the same
identicon (#81, #92), the source's output circle and the target's input circle; when
they come within the join distance on screen a solid capsule joins them, and it holds
while a view moves until they pass the larger break distance (hysteresis). Qt-free:
the pairs and the join/break decision; the QML side is test_port_capsule.py."""

from __future__ import annotations

from specto_app.canvas.capsules import (
    BREAK_PX,
    JOIN_PX,
    CapsuleTracker,
    joined_after,
    port_pairs,
)
from specto_app.canvas.snapping import GUTTER
from specto_app.theme import METRICS

INSET = METRICS["port_inset"]
STEP = METRICS["port_px"] + METRICS["port_stack"]


def link(src: str, dst: str, **extra) -> dict:
    return {
        "kind": "follow",
        "source": {"view": src, "port": "selection"},
        "target": {"view": dst, "port": "selection"},
        **extra,
    }


def side_by_side(gap: float) -> dict[str, tuple[float, float, float, float]]:
    """Two 400 x 300 views, tops aligned, ``gap`` canvas px between their rects."""
    return {"kobi": (0.0, 0.0, 400.0, 300.0), "tamu": (400.0 + gap, 0.0, 400.0, 300.0)}


def test_a_link_pairs_the_sources_output_circle_with_the_targets_input_circle():
    pairs = port_pairs(side_by_side(0), {"l1": link("kobi", "tamu")})
    assert pairs == [
        {
            "id": "l1",
            "link": "l1",
            "outView": "kobi",
            "inView": "tamu",
            "inIndex": 0,
            # snapped flush, the circles sit two gutters apart on one line
            "x1": 400.0 - GUTTER,
            "y1": GUTTER + INSET,
            "x2": 400.0 + GUTTER,
            "y2": GUTTER + INSET,
        }
    ]


def test_a_two_way_link_pairs_both_ways_and_the_project_source_pairs_nothing():
    """A two-way link gives its source an input circle (the target's identicon) and the
    target's output carries it too: a second pair, at the place that circle takes in the
    source's stack. The project node has no port circle (#127): its links pair nothing,
    and a link to a view not on the canvas pairs nothing."""
    rects = side_by_side(0)
    links = {
        "l1": link("project", "kobi"),
        "l2": link("kobi", "tamu", two_way=True),
        "l3": link("kobi", "gone"),
    }
    pairs = {p["id"]: p for p in port_pairs(rects, links)}
    assert set(pairs) == {"l2", "l2:back"}
    back = pairs["l2:back"]
    assert (back["link"], back["outView"], back["inView"], back["inIndex"]) == (
        "l2",
        "tamu",
        "kobi",
        1,  # under the project source's circle
    )
    assert (back["x1"], back["y1"]) == (800.0 - GUTTER, GUTTER + INSET)
    assert (back["x2"], back["y2"]) == (GUTTER, GUTTER + INSET + STEP)
    assert pairs["l2"]["inIndex"] == 0


def test_ports_join_within_the_join_distance_and_hold_until_the_break_distance():
    """The proposed distances: a join within 32 screen px between the circles' centres
    (a gap of at most 12 px between two 20 px circles, the canvas padding), a break past
    56 px (24 px more pull than the join, over twice the 10 px snap, so a snapping drag
    never flickers it)."""
    assert (JOIN_PX, BREAK_PX) == (32.0, 56.0)
    links = {"l1": link("kobi", "tamu")}

    def step(joined: set[str], gap: float) -> set[str]:
        return joined_after(joined, port_pairs(side_by_side(gap), links))

    # the circles' centres are gap + 2 gutters apart
    assert step(set(), 20) == {"l1"}  # 24 px: the worked example's spacing joins
    assert step(set(), 30) == set()  # 34 px: too far to join
    assert step({"l1"}, 30) == {"l1"}  # joined, it holds
    assert step({"l1"}, 52) == {"l1"}  # 56 px: still holding, at the break distance
    assert step({"l1"}, 53) == set()  # 57 px: it splits
    # the distance is the straight line between the centres, any direction
    rects = {"kobi": (0.0, 0.0, 400.0, 300.0), "tamu": (404.0, 24.0, 400.0, 300.0)}
    assert joined_after(set(), port_pairs(rects, links)) == {"l1"}  # (8, 24): 25.3 px
    rects["tamu"] = (404.0, 34.0, 400.0, 300.0)
    assert joined_after(set(), port_pairs(rects, links)) == set()  # (8, 34): 34.9 px


def test_the_distances_are_screen_px_at_the_current_zoom():
    """``ui`` is canvas px per screen px: zoomed out to 40 % (the overworld's default),
    canvas px count for less on screen."""
    links = {"l1": link("kobi", "tamu")}
    pairs = port_pairs(side_by_side(66), links)  # 70 canvas px apart
    assert joined_after(set(), pairs) == set()
    assert joined_after(set(), pairs, ui=2.5) == {"l1"}  # 28 screen px
    assert joined_after({"l1"}, port_pairs(side_by_side(136), links), ui=2.5) == {"l1"}
    assert joined_after({"l1"}, port_pairs(side_by_side(137), links), ui=2.5) == set()


def test_the_tracker_keeps_the_joined_set_across_a_drag_and_drops_pairs_that_go():
    """While a view is dragged the controller hands the tracker each step's pairs: it says
    when the joined set changed, and a link deleted mid-way leaves it."""
    links = {"l1": link("kobi", "tamu"), "l2": link("tamu", "pola")}
    tracker = CapsuleTracker()
    assert tracker.joined == set()

    def rects(gap: float) -> dict:
        r = side_by_side(gap)
        r["pola"] = (2000.0, 0.0, 300.0, 300.0)
        return r

    assert tracker.update(port_pairs(rects(100), links)) is False
    for gap, joined in [(60, set()), (20, {"l1"}), (40, {"l1"}), (60, set()), (40, set())]:
        tracker.update(port_pairs(rects(gap), links))
        assert tracker.joined == joined, gap
    assert tracker.update(port_pairs(rects(0), links)) is True
    assert tracker.update(port_pairs(rects(2), links)) is False  # still joined
    assert tracker.update(port_pairs(rects(2), {"l2": links["l2"]})) is True
    assert tracker.joined == set()
