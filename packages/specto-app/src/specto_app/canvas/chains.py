"""Link chains and wire geometry (#45; glossary: link chain, link node). Qt-free.

A link chain is a connected set of linked views; its links and view frames share one
colour in the normal view (faint curves, frame strokes) and in the overworld (clear
wires). Chains are numbered in document order of their first view, so the colour a
chain gets is the same in both levels and stable while the document only grows.

Ports are circles on a view's frame (glossary: port): one input circle per linked input
stacked down the left vertical edge and the output circle on the right, the first of each
``PORT_INSET`` canvas px below the frame's top edge (the frame is the view's rect inset by
the gutter). ``wire_geometry`` gives every link one curve in canvas px from the source
view's output circle to the input circle the link occupies on the target, leaving
rightwards and arriving from the left whatever the two views' relative position, with
the link node at the curve's middle; the wiring layer (#82) and the overworld draw it,
the normal view does not (#81). A link off the project
source leaves the project node (``PROJECT_NODE``, fixed at the canvas origin; #83).
"""

from __future__ import annotations

from typing import Any, Literal

from specto_core.ports import PROJECT_ID, PROJECT_NODE_RECT

from ..theme import METRICS
from .snapping import GUTTER

#: the theme carries this many chain colours (``chain_0`` .. ``chain_5`` and ``_soft``)
N_CHAINS = 6
#: the project node (glossary: project source, #83): fixed at the canvas origin, left of
#: the file tree (``canvas_document.FILE_TREE_X``); a tile showing the identicon seeded
#: "root" with no port of its own (#127), its wires leaving the node's right edge
PROJECT_NODE: tuple[float, float, float, float] = PROJECT_NODE_RECT
#: from the frame's top edge down to a port circle's centre (theme metric ``port_inset``)
PORT_INSET = float(METRICS["port_inset"])
#: from one stacked input circle's centre to the next (#81)
PORT_STEP = float(METRICS["port_px"] + METRICS["port_stack"])
#: a rect this short is a collapsed view's title bar (#114): its ports sit on its middle
COLLAPSED_H = float(METRICS["collapsed_h"])
#: a link node's clearance from the views, their headers and other nodes, screen px (#90)
NODE_PAD_PX = 6.0

Rect = tuple[float, float, float, float]
Direction = Literal["in", "out"]


def chains_of(views: dict[str, Any], links: dict[str, Any]) -> dict[str, int]:
    """view id -> chain number for every view that has at least one link; views without
    links are in no chain. The project source is not a view and joins none."""
    order = {vid: i for i, vid in enumerate(views)}
    parent: dict[str, str] = {}

    def find(v: str) -> str:
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    for link in links.values():
        ends = [link["source"]["view"], link["target"]["view"]]
        members = [v for v in ends if v in order]
        for v in members:
            parent.setdefault(v, v)
        if len(members) == 2:
            a, b = find(members[0]), find(members[1])
            if a != b:
                parent[a] = b
    roots: dict[str, list[str]] = {}
    for v in parent:
        roots.setdefault(find(v), []).append(v)
    components = sorted(roots.values(), key=lambda vs: min(order[v] for v in vs))
    return {v: i for i, vs in enumerate(components) for v in vs}


def chain_token(index: int) -> str:
    return f"chain_{index % N_CHAINS}"


def chain_tokens(chains: dict[str, int]) -> dict[str, str]:
    return {v: chain_token(i) for v, i in chains.items()}


def port_anchor(rect: Rect, direction: Direction, index: int = 0) -> tuple[float, float]:
    """The centre of a view's port circle in canvas px: on the frame's left edge for
    ``in``, its right edge for ``out``, ``PORT_INSET`` below the frame's top; input
    circle ``index`` sits that many circles further down the stack (#81). On a collapsed
    view's title bar (#114) they sit on the bar's middle, input ``index`` that many
    circles further right, inside the bar (outside, they would cover the output circle
    of a view snapped to its left)."""
    x, y, w, h = rect
    px = x + GUTTER if direction == "in" else x + w - GUTTER
    if h <= COLLAPSED_H:
        return (float(px + index * PORT_STEP), float(y + h / 2))
    return (float(px), float(y + GUTTER + PORT_INSET + index * PORT_STEP))


def input_indices(links: dict[str, Any]) -> dict[str, int]:
    """link id -> the place of its input circle in the target view's stack: one circle
    per link into a view, in document order, a two-way link taking one on its source
    view as well (``ports_info.input_ports`` lists them in the same order)."""
    counts: dict[str, int] = {}
    out: dict[str, int] = {}
    for lid, link in links.items():
        t = link["target"]["view"]
        out[lid] = counts.get(t, 0)
        counts[t] = out[lid] + 1
        if link.get("two_way"):
            s = link["source"]["view"]
            counts[s] = counts.get(s, 0) + 1
    return out


def project_refresh_anchor() -> tuple[float, float]:
    """The centre of the project node's refresh circle in canvas px (#127): a port-like
    circle at the node's bottom centre, clear of the wires leaving the middle of its
    right edge."""
    x, y, w, h = PROJECT_NODE
    return (float(x + w / 2), float(y + h))


def project_block() -> Rect:
    """The project node with its refresh circle: what a link node keeps clear of, like
    a card's header (#83)."""
    x, y, w, h = PROJECT_NODE
    cx, cy = project_refresh_anchor()
    half = float(METRICS["port_px"]) / 2
    return (float(x), float(y), max(w, cx + half - x), max(h, cy + half - y))


def project_anchor() -> tuple[float, float]:
    """The centre of the project node's output: the middle of its right edge."""
    x, y, w, h = PROJECT_NODE
    return (float(x + w), float(y + h / 2))


def wire_geometry(rects: dict[str, Rect], links: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per link whose views are on the canvas: ``{id, source, target, x1, y1,
    x2, y2, orient, nx, ny}`` in canvas px, from the source's output port to the input
    circle the link occupies on the target (``orient`` is always ``h``: the curve leaves
    rightwards and arrives from the left); ``(nx, ny)`` is the link node, the curve's
    middle (the controls are symmetric, so it is the straight middle)."""
    out: list[dict[str, Any]] = []
    indices = input_indices(links)
    for lid, link in links.items():
        s, t = link["source"]["view"], link["target"]["view"]
        dst = rects.get(t)
        if dst is None:
            continue
        x2, y2 = port_anchor(dst, "in", indices[lid])
        if s == PROJECT_ID:
            x1, y1 = project_anchor()
        else:
            src = rects.get(s)
            if src is None:
                continue
            x1, y1 = port_anchor(src, "out")
        orient = "h"
        out.append(
            {
                "id": lid,
                "source": s,
                "target": t,
                "x1": float(x1),
                "y1": float(y1),
                "x2": float(x2),
                "y2": float(y2),
                "orient": orient,
                "nx": (x1 + x2) / 2,
                "ny": (y1 + y2) / 2,
            }
        )
    return out


#: the link node's candidate spots along its wire, the middle first, never the ends
NODE_STEPS = [0.5, *(0.5 + s * d for s in range(1, 9) for d in (-0.05, 0.05))]

Size = tuple[float, float]


def curve_point(wire: dict[str, Any], t: float) -> tuple[float, float]:
    """The point at ``t`` (0 = source end, 1 = target end) of the cubic LinkCurve.qml
    draws for ``wire``: the controls lie ``bend`` right of the start and left of the end
    at their own heights, ``bend`` a third of the run and at least 24 px."""
    x1, y1, x2, y2 = wire["x1"], wire["y1"], wire["x2"], wire["y2"]
    bend = max(24.0, abs(x2 - x1) / 3)
    u = 1 - t
    b, c, d = 3 * u * u * t, 3 * u * t * t, t * t * t
    # the weights of the far end sum to c + d; the bend pulls by b - c (zero at the
    # middle), so the middle and a flat wire's height come out exact
    far = c + d
    return (x1 + (x2 - x1) * far + bend * (b - c), y1 + (y2 - y1) * far)


#: rects closer than this to merely touching still count as touching (float rounding)
TOUCH_EPS = 1e-6


def _overlaps(a: Rect, b: Rect) -> bool:
    """True when ``a`` and ``b`` share area; rects that touch (within ``TOUCH_EPS``) do
    not, so a node nudged flush under a header is accepted at any zoom."""
    e = TOUCH_EPS
    return (
        a[0] < b[0] + b[2] - e
        and b[0] < a[0] + a[2] - e
        and a[1] < b[1] + b[3] - e
        and b[1] < a[1] + a[3] - e
    )


def _inflate(r: Rect, pad: float) -> Rect:
    return (r[0] - pad, r[1] - pad, r[2] + 2 * pad, r[3] + 2 * pad)


def port_blocks(rects: dict[str, Rect], inputs: dict[str, int]) -> list[Rect]:
    """The port circles of every card as rects in canvas px (#81 layout): ``inputs[v]``
    input circles stacked down the frame's left edge (one hollow circle when nothing is
    linked) and one output circle on the right edge."""
    size = float(METRICS["port_px"])
    out: list[Rect] = []
    for vid, rect in rects.items():
        centres = [port_anchor(rect, "in", i) for i in range(max(1, inputs.get(vid, 0)))]
        centres.append(port_anchor(rect, "out"))
        out.extend((cx - size / 2, cy - size / 2, size, size) for cx, cy in centres)
    return out


def place_nodes(
    wires: list[dict[str, Any]],
    rects: dict[str, Rect],
    sizes: dict[str, Size],
    *,
    header: float,
    pad: float = 0.0,
    blocks: list[Rect] = (),
    bounds: Rect | None = None,
) -> dict[str, tuple[float, float]]:
    """Where each wire's link dot sits (#90, #121): ``link id -> (cx, cy)`` in canvas
    px, for a dot ``sizes[id]`` wide and high (a point when unknown).

    A dot always sits on its wire: at the spot nearest the middle (``NODE_STEPS``)
    whose box clears, by ``pad``, every card's header (the ``header`` px below the top
    of every card rect, gutter included), every ``blocks`` rect (the port circles,
    ``port_blocks``, and the project node) and the dots placed before it; when no spot
    does, at the middle. With ``bounds`` (the visible canvas, canvas px) only spots
    whose box lies inside count, and a wire with none inside puts its dot at the middle
    pulled inside."""
    ports = [_inflate(b, pad) for b in blocks]
    heads = [_inflate((r[0], r[1], r[2], header + GUTTER), pad) for r in rects.values()] + ports
    if bounds is not None and not (bounds[2] > 0 and bounds[3] > 0):
        bounds = None  # an area not laid out yet bounds nothing
    placed: list[Rect] = []
    out: dict[str, tuple[float, float]] = {}

    def box(p: tuple[float, float], size: Size) -> Rect:
        return (p[0] - size[0] / 2, p[1] - size[1] / 2, size[0], size[1])

    def clear(r: Rect) -> bool:
        return not any(_overlaps(r, o) for o in [*heads, *placed])

    def inside(r: Rect) -> bool:
        if bounds is None:
            return True
        bx, by, bw, bh = bounds
        e = TOUCH_EPS
        return (
            r[0] >= bx - e
            and r[1] >= by - e
            and r[0] + r[2] <= bx + bw + e
            and (r[1] + r[3] <= by + bh + e)
        )

    def pulled_in(p: tuple[float, float], size: Size) -> tuple[float, float]:
        if bounds is None:
            return p
        bx, by, bw, bh = bounds
        x = min(max(p[0], bx + size[0] / 2), bx + bw - size[0] / 2)
        y = min(max(p[1], by + size[1] / 2), by + bh - size[1] / 2)
        return (x, y)

    for wire in wires:
        size = sizes.get(wire["id"], (0.0, 0.0))
        spots = [p for p in (curve_point(wire, t) for t in NODE_STEPS) if inside(box(p, size))]
        spot = next((p for p in spots if clear(box(p, size))), None)
        if spot is None:
            spot = spots[0] if spots else pulled_in((wire["nx"], wire["ny"]), size)
        out[wire["id"]] = spot
        placed.append(_inflate(box(spot, size), pad))
    return out


class NodePlacer:
    """The link nodes' places for one layer (#90), shared by the overworld and the
    wiring layer (#82): the dots' sizes as the QML reports them (``set_size``, canvas
    px), the screen scale (``set_scale``: canvas px per screen px, 1 in the normal view)
    and ``places`` cached until one of them, or the wires and views, change
    (``invalidate``). ``header_px`` and ``pad_px`` are screen px."""

    def __init__(self, header_px: float, pad_px: float) -> None:
        self.header_px = float(header_px)
        self.pad_px = float(pad_px)
        self.sizes: dict[str, Size] = {}
        self.ui = 1.0
        self._cache: dict[str, dict[str, float]] | None = None
        self._bounds: Rect | None = None

    def set_size(self, link_id: str, w: float, h: float) -> bool:
        """True when the dot's size changed (the places must be read again)."""
        size = (float(w), float(h))
        if self.sizes.get(link_id) == size:
            return False
        self.sizes[link_id] = size
        self._cache = None
        return True

    def set_scale(self, ui: float) -> bool:
        if not ui > 0 or ui == self.ui:
            return False
        self.ui = float(ui)
        self._cache = None
        return True

    def invalidate(self) -> None:
        self._cache = None

    def places(
        self,
        wires: list[dict[str, Any]],
        rects: dict[str, Rect],
        inputs: dict[str, int],
        bounds: Rect | None = None,
    ) -> dict[str, dict[str, float]]:
        """``link id -> {x, y}``, the dot centres in canvas px; ``inputs`` is each
        view's number of linked input circles (``port_blocks``), ``bounds`` the visible
        canvas the dots stay inside (#121)."""
        if bounds != self._bounds:
            self._bounds = bounds
            self._cache = None
        if self._cache is None:
            spots = place_nodes(
                wires,
                rects,
                self.sizes,
                header=self.header_px * self.ui,
                pad=self.pad_px * self.ui,
                blocks=[*port_blocks(rects, inputs), project_block()],
                bounds=bounds,
            )
            self._cache = {lid: {"x": x, "y": y} for lid, (x, y) in spots.items()}
        return self._cache


__all__ = [
    "NODE_PAD_PX",
    "NODE_STEPS",
    "N_CHAINS",
    "PORT_INSET",
    "PORT_STEP",
    "PROJECT_NODE",
    "Direction",
    "NodePlacer",
    "Size",
    "chain_token",
    "chain_tokens",
    "chains_of",
    "curve_point",
    "input_indices",
    "place_nodes",
    "port_anchor",
    "port_blocks",
    "project_anchor",
    "project_block",
    "project_refresh_anchor",
    "wire_geometry",
]
