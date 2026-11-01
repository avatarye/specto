"""Rasterise the Specto app icon into a multi-size .ico (#77, the S character since #170).

    uv run python tools/make_icon.py            # writes specto_app/assets/specto.ico
    uv run python tools/make_icon.py --png DIR  # also one PNG per size, for inspection

The art is the designer's, read by ``specto_app.logo`` from ``specto_app/assets/logo``:
``s-body.svg`` with ``eyes-open.svg`` for the larger sizes, the 16 px drawing ``s-16.svg``
(heavier stroke, bigger eyes) for the small ones; it sits on a tile in the app's
background colour, which keeps the ink legible on a dark taskbar. Swap the art by
replacing those files and running this again.

The body is drawn as the file draws it: a stroked centre line or a filled outline.
The sizes are not scaled from one vector: each is hinted. A stroke gets a whole number of
pixels, and every horizontal or vertical run of the path (a bar, a side, the flat tangent
where a curve meets one or turns) is moved so the edges there land on pixel boundaries;
the rest of the path follows piecewise-linearly. The eyes are whole-pixel dots kept a
pixel clear of the ink. So the 16 and 24 px taskbar and title-bar sizes stay crisp. The
body is painted by Qt's anti-aliasing rasteriser (exact coverage), the tile and the eyes
at 8x and box-reduced: axis-aligned edges stay exact and only curves are anti-aliased.
"""

from __future__ import annotations

import argparse
import bisect
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "packages" / "specto-app" / "src" / "specto_app" / "assets"
ICO = ASSETS / "specto.ico"
sys.path.insert(0, str(ROOT / "packages" / "specto-app" / "src"))
from specto_app.logo import Dot, Logo, Part, load_logo  # noqa: E402

TILE = (0xF4, 0xEF, 0xE6, 255)  # specto/color/bg
INK = (0x3A, 0x36, 0x30, 255)  # specto/color/text
AGENT = (0xB8, 0x6A, 0x2B, 255)  # specto/color/agent

# size -> (drawing, grid): the small (16 px) drawing or the full one, and the side of the
# mark's 100-unit grid as a fraction of the tile. The master (256) is the design's 64 %;
# the mark grows toward the 16 px drawing's 90 % as the icon shrinks. The stroke and the
# eyes are the drawing's, rounded to whole pixels; the tile's radius is 22 %.
SIZES: dict[int, tuple[str, float]] = {
    16: ("small", 0.90),
    20: ("small", 0.90),
    24: ("small", 0.86),
    32: ("small", 0.78),
    40: ("body", 0.70),
    48: ("body", 0.64),
    64: ("body", 0.64),
    256: ("body", 0.64),
}
TILE_RADIUS = 0.22
SUPERSAMPLE = 8
CURVE_STEPS = 48
#: the most ink coverage (0-255) an eye's own pixels and the pixel ring round it may hold
EYE_INK = 16
RING_INK = 96

Point = tuple[float, float]


def _hints(part: Part) -> tuple[set[float], set[float]]:
    """The path's x and y where it runs vertically or horizontally: its axis-aligned
    lines, and curve ends whose tangent is axis-aligned (a stroke's bars and sides, a
    filled outline's flat edges and extremes); and its outermost x and y on every side,
    wherever they fall (an angled round cap, a tip), so the shape's silhouette is crisp."""
    pts = [p for poly in part.subpaths(CURVE_STEPS) for p in poly]
    xs = {min(p[0] for p in pts), max(p[0] for p in pts)}
    ys = {min(p[1] for p in pts), max(p[1] for p in pts)}
    for kind, seg in part.segments():
        if kind == "L":
            (x0, y0), (x1, y1) = seg
            if y0 == y1:
                ys.add(y0)
            if x0 == x1:
                xs.add(x0)
            continue
        p0, c1, c2, p1 = seg
        for end, ctrl in ((p0, c1), (p1, c2)):
            if ctrl[0] == end[0]:
                xs.add(end[0])
            if ctrl[1] == end[1]:
                ys.add(end[1])
    return xs, ys


def _axis(hints: set[float], origin: float, scale: float, w: int) -> Callable[[float], float]:
    """Grid units -> px along one axis: each hint moved so the edges at it (a stroke's on
    either side, ``w`` px apart; a filled outline's own, ``w`` 0) land on whole pixels,
    everything between interpolated. Hints that land on the same pixel (a flattened
    outline's run of nodes round one extreme, a cap beside a bar) keep one: the
    outermost at either end of the axis, so the silhouette's own edge is the exact one,
    else the first."""
    anchors: list[tuple[float, float]] = []
    top = max(hints, default=0.0)
    for c in sorted(hints):
        v = round(origin + c * scale - w / 2) + w / 2
        if not anchors or v > anchors[-1][1]:
            anchors.append((c, v))
        elif c == top:
            anchors[-1] = (c, v)
    keys = [c for c, _ in anchors]

    def to_px(c: float) -> float:
        if not anchors:
            return origin + c * scale
        i = bisect.bisect_left(keys, c)
        if i < len(keys) and keys[i] == c:
            return anchors[i][1]
        if i == 0 or i == len(keys):
            k, v = anchors[0] if i == 0 else anchors[-1]
            return v + (c - k) * scale
        (k0, v0), (k1, v1) = anchors[i - 1], anchors[i]
        return v0 + (c - k0) * (v1 - v0) / (k1 - k0)

    return to_px


def _body_coverage(body: Part, size: int, at: Callable[[Point], Point], w: int) -> Image.Image:
    """How much of each pixel the S covers (0-255): its stroke ``w`` px wide, or its
    filled outline by the file's fill rule. Painted by Qt's anti-aliasing rasteriser,
    whose coverage is exact, so an edge on a pixel boundary leaves no fringe."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

    path = QPainterPath()
    for poly in body.subpaths(CURVE_STEPS):
        pts = [at(p) for p in poly]
        path.moveTo(*pts[0])
        for x, y in pts[1:]:
            path.lineTo(x, y)
        if body.filled:
            path.closeSubpath()
    path.setFillRule(
        Qt.FillRule.OddEvenFill if body.fill_rule == "evenodd" else Qt.FillRule.WindingFill
    )
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    white = QColor(255, 255, 255)
    if body.filled:
        painter.fillPath(path, white)
    else:
        pen = QPen(white, w)
        pen.setCapStyle(
            {"round": Qt.PenCapStyle.RoundCap, "square": Qt.PenCapStyle.SquareCap}.get(
                body.cap, Qt.PenCapStyle.FlatCap
            )
        )
        pen.setJoinStyle(
            {"round": Qt.PenJoinStyle.RoundJoin, "bevel": Qt.PenJoinStyle.BevelJoin}.get(
                body.join, Qt.PenJoinStyle.MiterJoin
            )
        )
        painter.strokePath(path, pen)
    painter.end()
    rgba = Image.frombuffer(
        "RGBA", (size, size), bytes(img.constBits()), "raw", "BGRA", img.bytesPerLine(), 1
    )
    return rgba.getchannel("A")


def _eye_boxes(
    eyes: tuple[Dot, ...], dia0: int, fx: Callable, fy: Callable, ink: Image.Image
) -> list[tuple[int, int, int]]:
    """Whole-pixel eye dots (x, y, side): at their hinted places, a pixel apart, on no
    ink and with a pixel of mostly clear space round them (the S's anti-aliased fringe
    may touch that ring, its solid pixels may not); nudged a pixel or two, then shrunk,
    until they fit."""
    size = ink.width

    def clear(x: int, y: int, dia: int) -> bool:
        if x < 1 or y < 1 or x + dia + 1 > size or y + dia + 1 > size:
            return False
        for i in range(x - 1, x + dia + 1):
            for j in range(y - 1, y + dia + 1):
                ring = i in (x - 1, x + dia) or j in (y - 1, y + dia)
                if ink.getpixel((i, j)) > (RING_INK if ring else EYE_INK):
                    return False
        return True

    for dia in range(dia0, 1, -1):
        lefts = [round(fx(e.cx) - dia / 2) for e in eyes]
        for i in range(1, len(lefts)):
            lefts[i] = max(lefts[i], lefts[i - 1] + dia + 1)
        tops = [round(fy(e.cy) - dia / 2) for e in eyes]
        for dx in (0, -1, 1, -2, 2):
            for dy in (0, -1, 1):
                if all(clear(x + dx, y + dy, dia) for x, y in zip(lefts, tops, strict=True)):
                    return [(x + dx, y + dy, dia) for x, y in zip(lefts, tops, strict=True)]
    return [(x, y, 2) for x, y in zip(lefts, tops, strict=True)]


@dataclass(frozen=True)
class Layout:
    """One size's hinted drawing: the body and eyes it uses, its stroke in px (0 for a
    filled outline) and the grid -> px maps along x and y."""

    body: Part
    eyes: tuple[Dot, ...]
    scale: float
    w: int
    fx: Callable[[float], float]
    fy: Callable[[float], float]


def layout(size: int, art: Logo) -> Layout:
    which, grid = SIZES[size]
    drawing = art.small if which == "small" else art.body
    body = drawing.body
    assert body is not None
    eyes = drawing.eyes if which == "small" else art.open
    scale = grid * size / 100
    origin = size / 2 - 50 * scale
    w = 0 if body.filled else max(1, round(body.width * scale))
    hx, hy = _hints(body)
    return Layout(body, eyes, scale, w, _axis(hx, origin, scale, w), _axis(hy, origin, scale, w))


def draw(size: int, art: Logo) -> Image.Image:
    lay = layout(size, art)
    body, eyes, scale, w, fx, fy = lay.body, lay.eyes, lay.scale, lay.w, lay.fx, lay.fy
    s = SUPERSAMPLE

    tile = Image.new("RGBA", (size * s, size * s), (0, 0, 0, 0))
    tile_r = round(TILE_RADIUS * size)
    ImageDraw.Draw(tile).rounded_rectangle(
        (0, 0, size * s - 1, size * s - 1), radius=tile_r * s, fill=TILE
    )
    im = tile.reduce(s)
    ink = _body_coverage(body, size, lambda p: (fx(p[0]), fy(p[1])), w)
    im.paste(INK, (0, 0), ink)

    # the eyes: a 2 px dot is a square; bigger ones are round
    eye_mask = Image.new("L", (size * s, size * s), 0)
    d = ImageDraw.Draw(eye_mask)
    for x, y, dia in _eye_boxes(eyes, max(2, round(2 * eyes[0].r * scale)), fx, fy, ink):
        box = (x * s, y * s, (x + dia) * s - 1, (y + dia) * s - 1)
        (d.ellipse if dia > 2 else d.rectangle)(box, fill=255)
    im.paste(AGENT, (0, 0), eye_mask.reduce(s))
    return im


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ICO, help=f"the .ico to write (default {ICO})")
    ap.add_argument("--png", type=Path, default=None, help="also write <DIR>/specto-<size>.png")
    ap.add_argument("--art", type=Path, default=None, help="a folder of design SVGs to use")
    ns = ap.parse_args(argv)
    art = load_logo(ns.art) if ns.art else load_logo()
    sizes = sorted(SIZES)
    frames = {size: draw(size, art) for size in sizes}
    if ns.png is not None:
        ns.png.mkdir(parents=True, exist_ok=True)
        for size, im in frames.items():
            im.save(ns.png / f"specto-{size}.png")
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    # Pillow keeps only the sizes up to the base image's, so the base is the 256; the other
    # frames are matched to their size from append_images, never scaled
    *rest, first = (frames[s] for s in sizes)
    first.save(
        ns.out,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=rest,
        bitmap_format="bmp",
    )
    print(f"[OK] wrote {ns.out} ({', '.join(str(s) for s in sizes)} px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
