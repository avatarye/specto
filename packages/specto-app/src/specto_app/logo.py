"""The Specto mark, the S character (#170), read from its design files. No Qt.

``assets/logo/`` holds the designer's export verbatim (Figma page 04 "Logo final: S
character"): to change the art,
replace those files. Everything the app draws comes from them: the agent icon
(``AgentIcon.qml`` / ``SMark.qml`` through the ``logoArt`` context property), the panel
and welcome marks, and the window icon (``tools/make_icon.py``).

The parts share one 100-unit grid, placed on each file's canvas by the ``#mark`` group's
``translate(o o) scale(s)``:

- ``s-body.svg``: the S (``#s-body``), either a stroked centre line (its width, caps
  and joins) or a filled outline (``fill`` set, no stroke; nonzero or even-odd), as the
  file draws it;
- ``eyes-open.svg`` / ``eyes-wide.svg``: two circles (``#eye-left``, ``#eye-right``); the
  open eyes' comment gives the eyes' travel (an ellipse, "semi-axes (6, 7)", or the
  older box, "dx -8..+8, dy -9..+9"); the wide eyes' comment may give theirs ("an
  ellipse up to (4.7, 5.5)"), else they hold still;
- ``eyes-blink.svg``: the shut eyes, two strokes (their width over the open eye's
  diameter is how far a blink closes the lid);
- ``eyes-x.svg``: the error eyes, one stroked path per eye;
- ``s-16.svg``: the small drawing (heavier stroke, bigger eyes) for marks under ~24 px.

``Part.distance`` measures any point against the drawn shape (stroke or fill), which the
tests use to keep the eyes clear of the S wherever they move.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from typing import Any

LOGO_DIR = Path(__file__).resolve().parent / "assets" / "logo"
_SVG = "{http://www.w3.org/2000/svg}"
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"

Point = tuple[float, float]


class LogoError(ValueError):
    """A design file is missing a part the app draws."""


@dataclass(frozen=True)
class Dot:
    cx: float
    cy: float
    r: float


@dataclass(frozen=True)
class Part:
    """One path of the art, drawn as the file draws it: stroked along its centre line
    (``width``, ``cap``, ``join``) or, when ``filled``, filled by ``fill_rule``."""

    d: str
    width: float
    cap: str = "butt"
    join: str = "miter"
    filled: bool = False
    #: nonzero | evenodd
    fill_rule: str = "nonzero"

    def segments(self) -> list[tuple[str, tuple[Point, ...]]]:
        return path_segments(self.d)

    def bounds(self) -> tuple[float, float, float, float]:
        """The path's control-point box (x0, y0, x1, y1)."""
        pts = [p for _, seg in self.segments() for p in seg]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def subpaths(self, steps: int = 32) -> list[list[Point]]:
        """The path flattened into polylines, one per connected run of segments."""
        out: list[list[Point]] = []
        for kind, seg in self.segments():
            if not out or out[-1][-1] != seg[0]:
                out.append([seg[0]])
            if kind == "L":
                out[-1].append(seg[1])
            else:
                out[-1] += [cubic(seg, t / steps) for t in range(1, steps + 1)]
        return out

    def inside(self, p: Point) -> bool:
        """Whether a filled part covers ``p`` (by its fill rule)."""
        winding = 0
        for poly in self.subpaths():
            ring = poly if poly[0] == poly[-1] else [*poly, poly[0]]
            for (x0, y0), (x1, y1) in pairwise(ring):
                if (y0 <= p[1]) != (y1 <= p[1]):
                    x = x0 + (p[1] - y0) * (x1 - x0) / (y1 - y0)
                    if x > p[0]:
                        winding += 1 if y1 > y0 else -1
        return winding % 2 == 1 if self.fill_rule == "evenodd" else winding != 0

    def distance(self, p: Point) -> float:
        """How far ``p`` is from the drawn shape's edge, negative inside it: the stroke's
        half width around the centre line (round caps), or the filled outline."""
        edge = min(
            _to_segment(p, a, b)
            for poly in self.subpaths()
            for a, b in _pairs(poly, closed=self.filled)
        )
        if self.filled:
            return -edge if self.inside(p) else edge
        return edge - self.width / 2


@dataclass(frozen=True)
class Drawing:
    """One file's mark: where the grid sits on its canvas, the S, the eyes."""

    canvas: float
    origin: float
    scale: float
    body: Part | None
    eyes: tuple[Dot, ...]


@dataclass(frozen=True)
class Logo:
    body: Drawing
    small: Drawing
    open: tuple[Dot, ...]
    wide: tuple[Dot, ...]
    blink: tuple[Part, ...]
    x: tuple[Part, ...]
    #: how far both eyes may move from rest, in grid units (dx, dy)
    travel: tuple[float, float]
    #: how far the wide eyes may move; (0, 0) when the art gives them no travel
    wide_travel: tuple[float, float] = (0.0, 0.0)

    @property
    def blink_lid(self) -> float:
        """The open eyes' height at the blink's key pose (1 = open)."""
        return min(1.0, self.blink[0].width / (2 * self.open[0].r))

    def to_qml(self) -> dict[str, Any]:
        def dots(ds: tuple[Dot, ...]) -> list[dict[str, float]]:
            return [{"cx": d.cx, "cy": d.cy, "r": d.r} for d in ds]

        def stroke(s: Part) -> dict[str, Any]:
            x0, y0, x1, y1 = s.bounds()
            return {
                "path": s.d,
                "width": s.width,
                "cap": s.cap,
                "join": s.join,
                "filled": s.filled,
                "fillRule": s.fill_rule,
                "cx": (x0 + x1) / 2,
                "cy": (y0 + y1) / 2,
            }

        assert self.body.body is not None and self.small.body is not None
        return {
            # the grid on the agent tile, as fractions of the tile's side
            "origin": self.body.origin / self.body.canvas,
            "unit": self.body.scale / self.body.canvas,
            "body": stroke(self.body.body),
            "small": {**stroke(self.small.body), "eyes": dots(self.small.eyes)},
            "open": dots(self.open),
            "wide": dots(self.wide),
            "blinkLid": self.blink_lid,
            "x": [stroke(s) for s in self.x],
            "travel": list(self.travel),
            "wideTravel": list(self.wide_travel),
        }


# -- path data --------------------------------------------------------------------------------
def path_segments(d: str) -> list[tuple[str, tuple[Point, ...]]]:
    """An SVG path's lines and cubic curves in absolute coordinates: ("L", (p0, p1)) and
    ("C", (p0, c1, c2, p1)). Handles M L H V C Z, absolute and relative."""
    tokens = re.findall(rf"[MmLlHhVvCcZz]|{_NUM}", d)
    out: list[tuple[str, tuple[Point, ...]]] = []
    i, cmd = 0, ""
    cur: Point = (0.0, 0.0)
    start: Point = cur

    def num() -> float:
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if cur != start:
                    out.append(("L", (cur, start)))
                cur = start
                continue
        if not cmd:
            raise LogoError(f"path data starts without a command: {d[:40]}")
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = cur if rel else (0.0, 0.0)
        if c == "M":
            cur = start = (ox + num(), oy + num())
            cmd = "l" if rel else "L"  # further pairs are lines
        elif c == "L":
            p = (ox + num(), oy + num())
            out.append(("L", (cur, p)))
            cur = p
        elif c == "H":
            p = ((cur[0] if rel else 0.0) + num(), cur[1])
            out.append(("L", (cur, p)))
            cur = p
        elif c == "V":
            p = (cur[0], (cur[1] if rel else 0.0) + num())
            out.append(("L", (cur, p)))
            cur = p
        elif c == "C":
            c1 = (ox + num(), oy + num())
            c2 = (ox + num(), oy + num())
            p = (ox + num(), oy + num())
            out.append(("C", (cur, c1, c2, p)))
            cur = p
        else:
            raise LogoError(f"unsupported path command {cmd!r}")
    return out


def cubic(seg: tuple[Point, ...], t: float) -> Point:
    p0, c1, c2, p1 = seg
    u = 1 - t
    a, b, c, e = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (
        a * p0[0] + b * c1[0] + c * c2[0] + e * p1[0],
        a * p0[1] + b * c1[1] + c * c2[1] + e * p1[1],
    )


def _pairs(poly: list[Point], *, closed: bool = False) -> list[tuple[Point, Point]]:
    """The polyline's edges; an outline (``closed``) also runs back to its start."""
    ring = [*poly, poly[0]] if closed or len(poly) == 1 else poly
    return list(pairwise(ring))


def _to_segment(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def inside_points(part: Part, depth: float, step: float = 2.0) -> list[Point]:
    """Grid points the drawn shape covers at least ``depth`` units in from its edge."""
    x0, y0, x1, y1 = part.bounds()
    pts = []
    y = y0
    while y <= y1:
        x = x0
        while x <= x1:
            if part.distance((x, y)) <= -depth:
                pts.append((x, y))
            x += step
        y += step
    return pts


# -- the files --------------------------------------------------------------------------------
def _mark(root: ET.Element, path: Path) -> tuple[ET.Element, float, float]:
    g = next((e for e in root.iter(f"{_SVG}g") if e.get("id") == "mark"), None)
    if g is None:
        raise LogoError(f"{path.name}: no <g id='mark'>")
    tf = g.get("transform", "")
    t = re.search(rf"translate\(\s*({_NUM})", tf)
    s = re.search(rf"scale\(\s*({_NUM})", tf)
    return g, float(t.group(1)) if t else 0.0, float(s.group(1)) if s else 1.0


def _by_id(g: ET.Element, tag: str, id_: str, path: Path) -> ET.Element:
    e = next((e for e in g.iter(f"{_SVG}{tag}") if e.get("id") == id_), None)
    if e is None:
        raise LogoError(f"{path.name}: no <{tag} id='{id_}'>")
    return e


def _style(e: ET.Element) -> dict[str, str]:
    """Presentation attributes, with a ``style`` attribute's declarations winning."""
    out = dict(e.attrib)
    for decl in e.get("style", "").split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _part(e: ET.Element) -> Part:
    """A path as the file draws it: filled when it has a fill and no stroke."""
    a = _style(e)
    stroked = a.get("stroke", "none") != "none"
    filled = not stroked and a.get("fill", "black") != "none"
    return Part(
        a.get("d", ""),
        float(a.get("stroke-width", "1")) if stroked else 0.0,
        a.get("stroke-linecap", "butt"),
        a.get("stroke-linejoin", "miter"),
        filled,
        a.get("fill-rule", "nonzero"),
    )


def _eyes(g: ET.Element, path: Path) -> tuple[Dot, ...]:
    out = []
    for id_ in ("eye-left", "eye-right"):
        c = _by_id(g, "circle", id_, path)
        out.append(Dot(float(c.get("cx", 0)), float(c.get("cy", 0)), float(c.get("r", 0))))
    return tuple(out)


def _drawing(path: Path, *, eyes: bool) -> Drawing:
    root = ET.parse(path).getroot()
    g, origin, scale = _mark(root, path)
    canvas = float(root.get("width", "0").removesuffix("px"))
    body = _part(_by_id(g, "path", "s-body", path))
    return Drawing(canvas, origin, scale, body, _eyes(g, path) if eyes else ())


def _travel(path: Path, *, required: bool = True) -> tuple[float, float]:
    """The eyes' travel from a file's comment: an ellipse's semi-axes ("the ELLIPSE with
    semi-axes (6, 7) units", "an ellipse up to (4.7, 5.5) units"), or the older box ("dx
    -8..+8, dy -9..+9"), whose half-sides the gaze uses as an ellipse's. A file that need
    not state one (the wide eyes) gives (0, 0): no travel."""
    text = path.read_text(encoding="utf-8")
    m = re.search(rf"(?:semi-axes|ellipse up to)\s*\(\s*({_NUM})\s*,\s*({_NUM})\s*\)", text)
    if m is not None:
        return (abs(float(m.group(1))), abs(float(m.group(2))))
    m = re.search(rf"dx\s*({_NUM})\s*\.\.\s*({_NUM})\s*,\s*dy\s*({_NUM})\s*\.\.\s*({_NUM})", text)
    if m is None and not required:
        return (0.0, 0.0)
    if m is None:
        raise LogoError(
            f"{path.name}: no eye travel ('semi-axes (6, 7)' or 'dx -8..+8, dy -9..+9') "
            "in its comment"
        )
    x0, x1, y0, y1 = (float(v) for v in m.groups())
    return (min(abs(x0), abs(x1)), min(abs(y0), abs(y1)))


def load_logo(folder: Path = LOGO_DIR) -> Logo:
    """Read the mark from ``folder``; raises ``LogoError`` naming the missing part."""
    eyes: dict[str, Any] = {}
    for name in ("eyes-open", "eyes-wide"):
        p = folder / f"{name}.svg"
        g, _, _ = _mark(ET.parse(p).getroot(), p)
        eyes[name] = _eyes(g, p)
    for name in ("eyes-blink", "eyes-x"):
        p = folder / f"{name}.svg"
        g, _, _ = _mark(ET.parse(p).getroot(), p)
        eyes[name] = tuple(_part(_by_id(g, "path", i, p)) for i in ("eye-left", "eye-right"))
    return Logo(
        body=_drawing(folder / "s-body.svg", eyes=False),
        small=_drawing(folder / "s-16.svg", eyes=True),
        open=eyes["eyes-open"],
        wide=eyes["eyes-wide"],
        blink=eyes["eyes-blink"],
        x=eyes["eyes-x"],
        travel=_travel(folder / "eyes-open.svg"),
        wide_travel=_travel(folder / "eyes-wide.svg", required=False),
    )


@lru_cache(maxsize=1)
def logo() -> Logo:
    """The app's mark (``assets/logo``), read once."""
    return load_logo()


def gaze_offset(gx: float, gy: float, travel: tuple[float, float] | None = None) -> Point:
    """Where both eyes go for a gaze (x and y, 1 = toward the edge): a gaze inside the
    unit circle moves them that fraction of the travel; a longer one keeps its direction
    at full travel."""
    tx, ty = travel or logo().travel
    if not (math.isfinite(gx) and math.isfinite(gy)):
        return (0, 0)
    length = math.hypot(gx, gy)
    k = 1 / length if length > 1 else 1
    return (gx * k * tx, gy * k * ty)


__all__ = [
    "LOGO_DIR",
    "Dot",
    "Drawing",
    "Logo",
    "LogoError",
    "Part",
    "cubic",
    "gaze_offset",
    "inside_points",
    "load_logo",
    "logo",
    "path_segments",
]
