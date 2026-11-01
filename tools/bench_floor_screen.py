"""The tile floor on the real screen (#191, #197): a Specto window on an empty project, its
canvas panned one logical px a frame and its overworld panned a quarter px a frame and
zoomed slowly, each frame grabbed from the window (device px) and measured. A visible
window: run it under the desktop lock.

    uv run python tools/desktop_lock.py run grid191 -- uv run python tools/bench_floor_screen.py \\
        --work <fresh folder> [--scale 1.5]

``--scale`` sets QT_SCALE_FACTOR (the device pixel ratio on a 100 % display), to try the
fractional ratios of other displays. Per motion, on scanlines through an empty patch of
the floor: the tile and gap widths seen (device px), the share of frames whose tiles are
not all one width, the share of frame steps where the widths changed, and the largest
pixel difference between a frame and the first one (0: the floor, fixed to the screen
since #197, stayed still while the canvas moved under it).
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "specto-app" / "src"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--scale", type=float, default=0.0)
    ap.add_argument("--frames", type=int, default=48)
    ns = ap.parse_args()
    if ns.scale:
        os.environ["QT_SCALE_FACTOR"] = str(ns.scale)
    from specto_app.qt_setup import prepare

    prepare(web=False)
    from PySide6.QtGui import QGuiApplication

    from specto_app.app import SpectoApp, find_item
    from specto_core import Project

    app = QGuiApplication(["specto-floor-bench"])
    root = ns.work / f"proj-{int(time.time())}"
    project = Project.init(root, "floor-bench")
    sp = SpectoApp(project, jobs="inline", web=False, offscreen=False, serve=False)
    sp.start()
    win = sp.window
    win.resize(1280, 800)
    win.show()
    swapped = {"n": 0}
    win.frameSwapped.connect(lambda: swapped.__setitem__("n", swapped["n"] + 1))

    def settle(ms: float = 50) -> None:
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.002)

    def frame():
        n = swapped["n"]
        end = time.monotonic() + 1.0
        while swapped["n"] == n and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.001)
        settle(20)
        return win.grabWindow()

    settle(1500)
    dpr = win.devicePixelRatio()
    theme = sp._theme.tokens()["colors"]
    bg, tint = _rgb(theme["bg"]), _rgb(theme["bg_tint"])
    # an empty patch of floor: right of the file tree, above the hint card
    box = (600, 120, 1180, 520)

    def measure(img, shift: tuple[int, int] | None, first) -> dict:
        x0, y0, x1, y1 = (round(v * dpr) for v in box)
        widths, gaps = [], []
        diff = 0
        for y in range(y0, y1, 2):
            row = [_is_tile(img.pixelColor(x, y), bg, tint) for x in range(x0, x1)]
            t, g = _runs(row)
            widths += t
            gaps += g
        if shift is not None:
            sx, sy = shift
            for y in range(y0, y1, 5):
                for x in range(x0, x1, 5):
                    a, b = img.pixelColor(x + sx, y + sy), first.pixelColor(x, y)
                    diff = max(diff, abs(a.red() - b.red()) + abs(a.green() - b.green()))
        return {"tiles": sorted(set(widths)), "gaps": sorted(set(gaps)), "diff": diff}

    def summarise(frames: list[dict]) -> dict:
        changed = sum(1 for a, b in itertools.pairwise(frames) if a["tiles"] != b["tiles"])
        return {
            "tile_widths": sorted({w for f in frames for w in f["tiles"]}),
            "gap_widths": sorted({w for f in frames for w in f["gaps"]}),
            "frames_mixed_widths": round(sum(len(f["tiles"]) > 1 for f in frames) / len(frames), 3),
            "steps_widths_changed": round(changed / max(1, len(frames) - 1), 3),
            "max_shift_diff": max(f["diff"] for f in frames),
        }

    out = {"dpr": dpr, "scale_env": os.environ.get("QT_SCALE_FACTOR", "")}
    # 1. the canvas: a pan of one logical px a frame (the controller pans in whole px)
    controller = sp.controller
    px0, py0 = controller._pan
    first = frame()
    frames = [measure(first, (0, 0), first)]
    for f in range(1, ns.frames):
        controller.setPan(px0 - f, py0)  # the canvas moves right by f, the floor stays
        img = frame()
        frames.append(measure(img, (0, 0), first))
    out["canvas_pan_1px"] = summarise(frames)
    controller.setPan(px0, py0)
    # 2. the overworld: a quarter-px pan, then a slow zoom out
    sp.overworld.toggle()
    settle(800)
    over = find_item(win.contentItem(), "overworld")
    # the cards far off to the left and above: nothing but floor in the patch
    ox, oy = -3000.0, -3000.0
    over.setProperty("panX", ox)
    over.setProperty("panY", oy)
    over.setProperty("zoom", 0.5)
    first = frame()
    frames = [measure(first, (0, 0), first)]
    for f in range(1, ns.frames):
        over.setProperty("panX", ox + 0.25 * f)
        img = frame()
        frames.append(measure(img, (0, 0), first))
    out["overworld_pan_quarter_px"] = summarise(frames)
    first = frame()
    frames = []
    for f in range(ns.frames):
        over.setProperty("zoom", 0.6 - 0.35 * f / (ns.frames - 1))
        frames.append(measure(frame(), (0, 0), first))
    out["overworld_zoom_0.6_to_0.25"] = summarise(frames)
    print("RESULT " + json.dumps(out))
    sp.close()
    return 0


def _rgb(name: str) -> tuple[int, int, int]:
    name = name.lstrip("#")
    return tuple(int(name[i : i + 2], 16) for i in (0, 2, 4))


def _is_tile(c, bg, tint) -> bool:
    p = (c.red(), c.green(), c.blue())
    return sum((a - b) ** 2 for a, b in zip(p, tint, strict=True)) < sum(
        (a - b) ** 2 for a, b in zip(p, bg, strict=True)
    )


def _runs(row: list[bool]) -> tuple[list[int], list[int]]:
    """Interior run lengths of tile and gap pixels."""
    tiles, gaps, i, seg = [], [], 0, []
    while i < len(row):
        j = i
        while j < len(row) and row[j] == row[i]:
            j += 1
        seg.append((row[i], j - i))
        i = j
    for is_tile, n in seg[1:-1]:
        (tiles if is_tile else gaps).append(n)
    return tiles, gaps


if __name__ == "__main__":
    sys.exit(main())
