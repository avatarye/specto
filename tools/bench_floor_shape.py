"""The tile floor's shape prototype (#191): rounded tiles at 1/4 resolution against square
tiles snapped to the low-res pixel grid, under a scripted slow pan and a slow zoom,
rendered offscreen with OpenGL through the same Qt Quick layer path the floor uses (a
ShaderEffect with ``layer.textureSize`` = the item / 4, drawn back at 4x). No window
shows.

    uv run python tools/bench_floor_shape.py [--frames 64]

Variants (black tiles on white, so the measurement is exact; the geometry is the
design's: cell 16, tile 70 %, radius a quarter of the side):

- ``rounded-naive``: 1/4 res, the camera's sub-pixel phase in the shader, analytic AA at
  the low-res pixel's footprint, linear upscale (``layer.smooth``);
- ``rounded-snapped``: 1/4 res, the phase snapped to whole low-res pixels and the
  remainder taken by moving the layer's quad in whole screen px, the cell a whole number
  of low-res px (tile 3 of 4), corners from the same SDF with coverage AA, nearest upscale;
- ``square-snapped``: the same with hard square tiles (what the floor ships);
- ``rounded-full``: the design's round-1 reference, full resolution, analytic AA.

Per variant, over a pan of 0.25 px a frame (one cell in 64 frames) and a zoom from cell
16 to 12 px: the tile and gap widths seen on a scanline through the tile centres
(threshold at half coverage), the share of frame steps where some tile changed width
("flicker"), the shallowest gap (1 = the gap reaches the background colour, 0 = gone:
"smear"), the share of frames whose tiles are not all the same width, and the corner
contrast (how far the corner pixel is from the tile colour; 0 = no visible rounding).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "specto-app" / "src"))

from specto_app.frames.offscreen import OffscreenQuick, prepare_environment

prepare_environment()

from PySide6.QtCore import QSize, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

W, H = 320, 96

FRAG = """#version 440
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 texSize;   // the texture in its own px
    vec2 phase;     // the grid's origin in texture px (fractional in the naive variant)
    float cell;     // texture px per cell
    float side;     // tile side, texture px
    float radius;   // corner radius, texture px
    float aa;       // 1: analytic AA over one texture px; 0: hard edges
    float inset;    // the tile's offset in its cell (centred, or 0: on the low-res grid)
};
float sdRoundBox(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}
void main() {
    vec2 p = qt_TexCoord0 * texSize - phase;
    vec2 c = floor(p / cell) * cell + inset + side * 0.5;
    float sd = sdRoundBox(p - c, vec2(side * 0.5), radius);
    float a = aa > 0.5 ? clamp(0.5 - sd, 0.0, 1.0) : (sd < 0.0 ? 1.0 : 0.0);
    fragColor = vec4(vec3(1.0 - a), 1.0) * qt_Opacity;
}
"""

QML = """import QtQuick
Item {
    id: root
    width: %(w)d; height: %(h)d
    property bool layered: false
    property bool smoothUp: false
    property size texSize: Qt.size(1, 1)
    Rectangle { anchors.fill: parent; color: "white" }
    Item {
        objectName: "quad"
        ShaderEffect {
            objectName: "fx"
            layer.enabled: root.layered
            layer.smooth: root.smoothUp
            layer.textureSize: root.texSize
            property vector2d texSize: Qt.vector2d(1, 1)
            property vector2d phase: Qt.vector2d(0, 0)
            property real cell: 4
            property real side: 3
            property real radius: 0
            property real aa: 0
            property real inset: 0
            fragmentShader: "%(qsb)s"
        }
    }
}
"""


def compile_frag(tmp: Path) -> Path:
    src = tmp / "proto.frag"
    src.write_text(FRAG, encoding="utf-8")
    out = tmp / "proto.frag.qsb"
    tool = Path(sys.executable).parent / "pyside6-qsb"
    flags = ["--glsl", "100es,120,150", "--hlsl", "50", "--msl", "12"]
    subprocess.check_call([str(tool), *flags, "-o", str(out), str(src)])
    return out


def find(item, name):
    stack = [item]
    while stack:
        it = stack.pop()
        if it.objectName() == name:
            return it
        stack.extend(it.childItems())
    raise KeyError(name)


def setup(root, fx, quad, variant: str, pan: float, cell_px: float) -> None:
    """The scene for one frame: ``pan`` (screen px, the grid moves right), cell in screen px."""
    tile = 0.7 * cell_px
    if variant == "rounded-full":
        root.setProperty("layered", False)
        quad.setX(0)
        fx.setWidth(W)
        fx.setHeight(H)
        put(fx, (W, H), (pan, 0.0), cell_px, tile, tile / 4, 1)
        return
    tw, th = W // 4 + 1, H // 4 + 1
    fx.setWidth(tw * 4)
    fx.setHeight(th * 4)
    root.setProperty("layered", True)
    root.setProperty("texSize", QSize(tw, th))
    if variant == "rounded-naive":
        root.setProperty("smoothUp", True)
        quad.setX(0)
        put(fx, (tw, th), (pan / 4, 0.0), cell_px / 4, tile / 4, tile / 16, 1)
        return
    # snapped: the cell a whole number of low-res px, the phase whole, the rest a quad move
    root.setProperty("smoothUp", False)
    n = max(1, round(cell_px / 4))
    q = round(pan)
    m = q % 4
    quad.setX(m - 4)
    side = n - 1
    rounded = variant == "rounded-snapped"
    put(
        fx,
        (tw, th),
        ((q - m + 4) / 4, 0.0),
        n,
        side,
        side / 4 if rounded else 0,
        1 if rounded else 0,
        0,
    )


def put(fx, tex, phase, cell, side, radius, aa, inset=None) -> None:
    from PySide6.QtGui import QVector2D

    fx.setProperty("texSize", QVector2D(*tex))
    fx.setProperty("phase", QVector2D(*phase))
    fx.setProperty("cell", float(cell))
    fx.setProperty("side", float(side))
    fx.setProperty("radius", float(radius))
    fx.setProperty("aa", float(aa))
    fx.setProperty("inset", float((cell - side) / 2 if inset is None else inset))


def runs(row: list[float]) -> tuple[list[int], list[int], float]:
    """Tile and gap run lengths (interior runs only) on a coverage row (0 white .. 1
    black), and the shallowest gap: 1 - the darkest gap's lightest pixel's coverage."""
    on = [v >= 0.5 for v in row]
    tiles, gaps, depth = [], [], 1.0
    i = 0
    seg = []
    while i < len(on):
        j = i
        while j < len(on) and on[j] == on[i]:
            j += 1
        seg.append((on[i], i, j))
        i = j
    for k, (is_tile, a, b) in enumerate(seg):
        if k == 0 or k == len(seg) - 1:
            continue
        (tiles if is_tile else gaps).append(b - a)
        if not is_tile:
            depth = min(depth, 1.0 - min(row[a:b]))
    # a gap that vanished below threshold still shows as a dip inside a tile run
    return tiles, gaps, depth


def coverage_row(img, y: int) -> list[float]:
    return [1.0 - img.pixelColor(x, y).red() / 255.0 for x in range(8, W - 8)]


def measure(quick, root, variant: str, frames: int) -> dict:
    fx, quad = find(root, "fx"), find(root, "quad")
    out = {}
    for motion in ("pan", "zoom"):
        widths, gap_ws, depth, changes, mixed, corner = set(), set(), 1.0, 0, 0, []
        prev = None
        for f in range(frames):
            if motion == "pan":
                pan, cell = f * 0.25, 16.0
            else:
                pan, cell = 0.0, 16.0 - 4.0 * f / (frames - 1)
            setup(root, fx, quad, variant, pan, cell)
            quick.render()
            img = quick.grab()
            # the scanline through the tile centres of the second cell row: centred tiles
            # in [cell, 2 cell); snapped tiles fill the cell's first n - 1 low-res px
            if variant in ("rounded-snapped", "square-snapped"):
                n = round(cell / 4)
                y = 4 * n + 2 * (n - 1)
            else:
                y = int(1.5 * cell)
            row = coverage_row(img, y)
            tiles, gaps, d = runs(row)
            widths.update(tiles)
            gap_ws.update(gaps)
            depth = min(depth, d)
            if len(set(tiles)) > 1:
                mixed += 1
            if prev is not None and motion == "pan" and set(prev) != set(tiles):
                changes += 1
            prev = tiles
            if motion == "pan" and f == 0:
                corner.append(corner_contrast(img, variant))
        out[motion] = {
            "tile_widths": sorted(widths),
            "gap_widths": sorted(gap_ws),
            "shallowest_gap": round(depth, 3),
            "frames_mixed_widths": round(mixed / frames, 3),
        }
        if motion == "pan":
            out[motion]["frames_tile_changed"] = round(changes / (frames - 1), 3)
            out["corner_contrast"] = corner[0]
    return out


def corner_contrast(img, variant: str) -> float:
    """How much a tile's corner pixel lacks beside its edge pixel: 1 - coverage(corner) /
    coverage(edge), both on the tile's first pixel column (0: a square corner)."""
    # snapped: the tile of cell (1, 1) is screen [16, 28); centred: [18.4, 29.6)
    x0, mid = (16, 22) if variant in ("rounded-snapped", "square-snapped") else (18, 24)

    def cov(x, y):
        return 1.0 - img.pixelColor(x, y).red() / 255.0

    edge = cov(x0, mid)
    return round(1.0 - cov(x0, x0) / edge, 3) if edge > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--frames", type=int, default=64)
    ns = ap.parse_args()
    _app = QGuiApplication([])  # held for the run
    quick = OffscreenQuick(W, H)
    with tempfile.TemporaryDirectory() as tmp:
        qsb = compile_frag(Path(tmp))
        root = quick.load(QML % {"w": W, "h": H, "qsb": QUrl.fromLocalFile(str(qsb)).toString()})
        result = {}
        for variant in ("rounded-naive", "rounded-snapped", "square-snapped", "rounded-full"):
            result[variant] = measure(quick, root, variant, ns.frames)
            print(f"[OK] {variant}: {json.dumps(result[variant])}")
        quick.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
