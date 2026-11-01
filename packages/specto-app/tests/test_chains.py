"""Link chains (#45, glossary: link chain): connected sets of linked views share one colour
in both levels, and every link has one wire geometry both levels draw. Qt-free."""

from __future__ import annotations

import json
from pathlib import Path

from specto_app.canvas.chains import PORT_INSET, chain_tokens, chains_of, wire_geometry
from specto_app.canvas.snapping import GUTTER

ROOT = Path(__file__).resolve().parents[3]
COMPOSITION = ROOT / "tools" / "gui_flows" / "v2-40-canvas.json"


def worked_example() -> dict:
    return json.loads(COMPOSITION.read_text(encoding="utf-8"))


def test_linked_views_form_one_chain_and_unlinked_views_none():
    doc = worked_example()
    # the tree feeds the front gallery (one chain of two); the rear gallery is pinned off
    # the project source, which is not a view and joins no chain: a chain of its own
    assert chains_of(doc["views"], doc["links"]) == {"fege": 0, "kobi": 0, "tamu": 1}

    doc["views"]["rena"] = {"type": "gallery", "layout": {"position": [0, 900], "size": [300, 200]}}
    doc["views"]["pola"] = {"type": "image", "layout": {"position": [400, 900], "size": [300, 200]}}
    doc["views"]["mihu"] = {"type": "json", "layout": {"position": [800, 900], "size": [300, 200]}}
    doc["links"]["l4"] = {
        "kind": "follow",
        "source": {"view": "rena", "port": "selection"},
        "target": {"view": "pola", "port": "selection"},
        "summary": {"sentence": "The image follows the gallery."},
    }
    chains = chains_of(doc["views"], doc["links"])
    assert chains == {"fege": 0, "kobi": 0, "tamu": 1, "rena": 2, "pola": 2}
    assert "mihu" not in chains  # no links: no chain, a neutral frame
    tokens = chain_tokens(chains)
    assert tokens["fege"] == tokens["kobi"] == "chain_0" and tokens["rena"] == "chain_2"
    # a lone view fed only by the project source is a chain of one: coloured all the same,
    # so the file tree's frame and its intaker wire agree
    fresh = {"fege": doc["views"]["fege"]}
    assert chains_of(fresh, {"l1": doc["links"]["l1"]}) == {"fege": 0}


def test_wire_geometry_runs_from_the_output_port_to_the_input_port_with_a_node_at_the_middle():
    """Ports are circles on the frame's vertical edges near the top corners: the input
    on the left edge, the output on the right edge (user feedback on v2). A curve leaves
    the source's output port and arrives at the target's input port, whatever their
    relative position; the link node sits at the curve's middle."""
    from specto_app.canvas.chains import PROJECT_NODE, port_anchor, project_anchor
    from specto_app.canvas.snapping import GUTTER

    doc = worked_example()
    rects = {
        vid: (*v["layout"]["position"], *v["layout"]["size"]) for vid, v in doc["views"].items()
    }
    # the port centre: on the frame's edge (the rect inset by the gutter), PORT_INSET down
    assert port_anchor((0, 0, 280, 804), "in") == (GUTTER, GUTTER + PORT_INSET)
    assert port_anchor((0, 0, 280, 804), "out") == (280 - GUTTER, GUTTER + PORT_INSET)
    wires = {w["id"]: w for w in wire_geometry(rects, doc["links"])}
    # files (0,0 280x804) -> kobi (296,8 640x400): files's output port to kobi's input port
    w = wires["l2"]
    assert (w["x1"], w["y1"]) == port_anchor(rects["fege"], "out")
    assert (w["x2"], w["y2"]) == port_anchor(rects["kobi"], "in")
    assert w["orient"] == "h"
    assert (w["nx"], w["ny"]) == ((w["x1"] + w["x2"]) / 2, (w["y1"] + w["y2"]) / 2)
    assert (w["source"], w["target"]) == ("fege", "kobi")
    # the project source is the fixed node at the origin: its wire leaves the node's
    # right edge and ends at the tree's input port
    p = wires["l1"]
    assert (p["x2"], p["y2"]) == port_anchor(rects["fege"], "in")
    assert (p["x1"], p["y1"]) == project_anchor() and p["source"] == "project"
    x, y, w, h = PROJECT_NODE
    assert project_anchor() == (x + w, y + h / 2)
    # stacked views still leave the output (right) and arrive at the input (left)
    stacked = wire_geometry(
        {"a": (0, 0, 300, 200), "b": (20, 300, 300, 200)},
        {"l": {"source": {"view": "a", "port": "x"}, "target": {"view": "b", "port": "y"}}},
    )[0]
    assert stacked["orient"] == "h"
    assert (stacked["x1"], stacked["y1"]) == port_anchor((0, 0, 300, 200), "out")
    assert (stacked["x2"], stacked["y2"]) == port_anchor((20, 300, 300, 200), "in")
    # a link to a view that is gone draws nothing
    gone = {"l": {"source": {"view": "a", "port": "x"}, "target": {"view": "zz", "port": "y"}}}
    assert wire_geometry({"a": (0, 0, 1, 1)}, gone) == []


#: a link dot's click area in canvas px at 100 % (theme ``link_dot_hit``, #121)
DOT = (20.0, 20.0)


def rect_of(place: tuple[float, float], size: tuple[float, float]) -> tuple[float, ...]:
    (cx, cy), (w, h) = place, size
    return (cx - w / 2, cy - h / 2, w, h)


def overlaps(a, b) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def canvas_45() -> tuple[dict, dict]:
    doc = json.loads((ROOT / "tools" / "gui_flows" / "v2-45-canvas.json").read_text("utf-8"))
    rects = {
        vid: (*v["layout"]["position"], *v["layout"]["size"]) for vid, v in doc["views"].items()
    }
    return doc, rects


def on_wire(place: tuple[float, float], wire: dict) -> float | None:
    """The step ``t`` of ``NODE_STEPS`` whose curve point is ``place``, else None."""
    from specto_app.canvas.chains import NODE_STEPS, curve_point

    for t in NODE_STEPS:
        x, y = curve_point(wire, t)
        if abs(x - place[0]) < 1e-9 and abs(y - place[1]) < 1e-9:
            return t
    return None


def test_a_link_dot_sits_on_its_wire_nearest_the_middle_clear_of_headers_and_ports():
    """#121 (#90's rule for dots): a link dot sits on its wire, at the spot nearest the
    middle whose dot clears every view header and port circle (and the dots placed
    before it); when no spot on the wire does, at the middle. It never leaves the wire.
    Canvas v2-45: the tree at (80, 0), kobi at (376, 8) and tamu under it, rena and pola
    to the right, the project node at the origin."""
    from specto_app.canvas.chains import (
        NODE_STEPS,
        curve_point,
        place_nodes,
        port_blocks,
        project_block,
    )

    doc, rects = canvas_45()
    wires = wire_geometry(rects, doc["links"])
    by_id = {w["id"]: w for w in wires}
    header, pad = 30.0, 6.0
    ins = {"fege": 1, "kobi": 1, "tamu": 1, "rena": 2, "pola": 2}
    blocks = [*port_blocks(rects, ins), project_block()]
    sizes = dict.fromkeys(by_id, DOT)
    places = place_nodes(wires, rects, sizes, header=header, pad=pad, blocks=blocks)
    assert set(places) == set(by_id)
    heads = [
        (x - pad, y - pad, w + 2 * pad, header + GUTTER + 2 * pad) for x, y, w, _ in rects.values()
    ]
    keep_off = heads + [(b[0] - pad, b[1] - pad, b[2] + 2 * pad, b[3] + 2 * pad) for b in blocks]

    def clear(p) -> bool:
        return not any(overlaps(rect_of(p, DOT), o) for o in keep_off)

    for lid, place in places.items():
        w = by_id[lid]
        t = on_wire(place, w)
        assert t is not None, (lid, place)  # on the wire, always
        if clear(place):
            # no spot nearer the middle clears the headers and ports
            nearer = [s for s in NODE_STEPS if abs(s - 0.5) < abs(t - 0.5)]
            assert not any(clear(curve_point(w, s)) for s in nearer), lid
        else:
            assert t == 0.5 and not any(clear(curve_point(w, s)) for s in NODE_STEPS), lid
    # tree -> tamu runs down the gap over the cards' bodies: the middle is clear
    assert places["l3"] == (by_id["l3"]["nx"], by_id["l3"]["ny"])
    # project -> tree and tree -> kobi are short wires inside the header bands: nothing
    # on them clears, so their dots sit at the middle, still on the wire
    assert places["l1"] == (by_id["l1"]["nx"], by_id["l1"]["ny"])
    assert places["l2"] == (by_id["l2"]["nx"], by_id["l2"]["ny"])
    # the wires that leave the header bands find a clear spot; project -> rena (l4) runs
    # flat along them past kobi, so its dot takes the middle
    assert places["l4"] == (by_id["l4"]["nx"], by_id["l4"]["ny"])
    for lid in ("l3", "l5", "l6"):
        assert clear(places[lid]), lid
    # the curve the dots ride is the one LinkCurve.qml draws (symmetric controls)
    w = by_id["l3"]
    assert curve_point(w, 0.0) == (w["x1"], w["y1"]) and curve_point(w, 1.0) == (w["x2"], w["y2"])
    assert curve_point(w, 0.5) == (w["nx"], w["ny"])
    # a wire with no size recorded still gets a place (its middle when that is clear)
    assert place_nodes(wires, rects, {}, header=header, pad=pad)["l3"] == places["l3"]


def test_a_link_dot_stays_inside_the_viewport():
    """#121, with the top bar gone (#120): a wire whose middle lies outside the visible
    canvas puts its dot at the spot on the wire nearest the middle that is inside it;
    a wire with no such spot puts it at the middle pulled inside."""
    from specto_app.canvas.chains import NODE_STEPS, curve_point, place_nodes

    rects = {"a": (0.0, 0.0, 300.0, 300.0), "b": (600.0, 400.0, 300.0, 300.0)}
    links = {"l1": {"source": {"view": "a", "port": "x"}, "target": {"view": "b", "port": "y"}}}
    (wire,) = wire_geometry(rects, links)
    viewport = (0.0, wire["ny"] + 60.0, 2000.0, 2000.0)  # the middle is above it
    place = place_nodes([wire], rects, {"l1": DOT}, header=30.0, bounds=viewport)["l1"]
    vx, vy, vw, vh = viewport
    px, py, pw, ph = rect_of(place, DOT)
    assert vx <= px and vy <= py and px + pw <= vx + vw and py + ph <= vy + vh
    t = on_wire(place, wire)
    assert t is not None
    inside = [s for s in NODE_STEPS if rect_of(curve_point(wire, s), DOT)[1] >= vy]
    assert t == min(inside, key=lambda s: abs(s - 0.5))
    # a flat wire wholly above the viewport: the middle, pulled inside
    rects = {"a": (0.0, 0.0, 300.0, 300.0), "b": (600.0, 0.0, 300.0, 300.0)}
    (flat,) = wire_geometry(rects, links)
    bounds = (0.0, 100.0, 2000.0, 900.0)
    place = place_nodes([flat], rects, {"l1": DOT}, header=30.0, bounds=bounds)
    assert place["l1"] == (flat["nx"], 100.0 + DOT[1] / 2)


def test_a_wire_arrives_at_the_input_circle_its_link_occupies_in_the_stack():
    """A view with several linked inputs shows one input circle per link, stacked down
    the left edge (#81); the wire of each link arrives at its own circle (#82). A two-way
    link whose source is the view takes a circle on it too, in document order."""
    from specto_app.canvas.chains import port_anchor
    from specto_app.theme import METRICS

    step = METRICS["port_px"] + METRICS["port_stack"]
    rects = {"a": (0, 0, 300, 200), "b": (400, 0, 300, 200), "c": (800, 0, 300, 200)}
    links = {
        "l1": {"source": {"view": "a", "port": "x"}, "target": {"view": "b", "port": "y"}},
        "l2": {
            "two_way": True,
            "source": {"view": "b", "port": "s"},
            "target": {"view": "c", "port": "s"},
        },
        "l3": {"source": {"view": "c", "port": "x"}, "target": {"view": "b", "port": "z"}},
    }
    wires = {w["id"]: w for w in wire_geometry(rects, links)}
    x0, y0 = port_anchor(rects["b"], "in")
    assert (wires["l1"]["x2"], wires["l1"]["y2"]) == (x0, y0)  # b's first circle
    # the two-way link is b's second circle, so l3 arrives at the third
    assert (wires["l3"]["x2"], wires["l3"]["y2"]) == (x0, y0 + 2 * step)
    # and l2 arrives at c's first circle, leaving b's output as any link does
    assert (wires["l2"]["x2"], wires["l2"]["y2"]) == port_anchor(rects["c"], "in")
    assert (wires["l2"]["x1"], wires["l2"]["y1"]) == port_anchor(rects["b"], "out")
    assert wires["l3"]["ny"] == (wires["l3"]["y1"] + wires["l3"]["y2"]) / 2


def test_link_dots_keep_clear_of_the_project_node_and_its_refresh_circle():
    """#83, #121: the project node sits at the canvas origin with its refresh circle on
    its ring; a dot on a wire leaving it keeps clear of both when its wire allows
    (``NodePlacer``), and the short wire into the file tree keeps its dot at the middle."""
    from specto_app.canvas.chains import PROJECT_NODE, NodePlacer, project_block

    rects = {"fege": (80.0, 0.0, 280.0, 900.0), "kobi": (380.0, 300.0, 640.0, 400.0)}
    links = {
        "l1": {
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "fege", "port": "data"},
        },
        "l2": {
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "kobi", "port": "data"},
        },
    }
    placer = NodePlacer(30.0, 4.0)
    for lid in links:
        placer.set_size(lid, *DOT)
    wires = {w["id"]: w for w in wire_geometry(rects, links)}
    places = placer.places(list(wires.values()), rects, {"fege": 1, "kobi": 1})
    block = project_block()
    x, _, w, _ = PROJECT_NODE
    assert block[0] <= x and block[2] >= w
    p = places["l2"]
    assert not overlaps(rect_of((p["x"], p["y"]), DOT), block)
    assert on_wire((p["x"], p["y"]), wires["l2"]) is not None
    assert places["l1"] == {"x": wires["l1"]["nx"], "y": wires["l1"]["ny"]}
