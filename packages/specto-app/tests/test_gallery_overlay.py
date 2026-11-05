"""Gallery captions overlay the thumbnail (SPEC-6): light text over a dark scrim on the
thumb's bottom band, readable on a white image and a black one alike, and the current
(linked) item marked apart from the selection ring (a bar along its bottom edge).

The offscreen software renderer draws no gallery tiles, so the pixels come from a child
process on the real platform and the OpenGL scene graph (gallery_overlay_child.py: a tool
window parked off every monitor that never takes the focus); skipped where there is no
display or no GPU path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from specto_app.views import gallery_native

CHILD = Path(__file__).with_name("gallery_overlay_child.py")
#: the item's defaults, which the theme's tokens match (theme.py: now, image_scrim)
NOW = QColor("#e3a21a")
SELECTION = QColor("#2f6fab")

pytestmark = pytest.mark.skipif(not gallery_native.is_built(), reason=gallery_native.BUILD_HINT)


def luminance(c: QColor) -> float:
    """WCAG relative luminance of an sRGB colour."""

    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def near(a: QColor, b: QColor, tol: int = 40) -> bool:
    return abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue()) <= tol


class Tile:
    def __init__(self, img: QImage, rect: list[float], dpr: float, band: float) -> None:
        self.img = img
        self.x, self.y, self.w, self.h = (v * dpr for v in rect)
        self.band = band * dpr
        self.dpr = dpr

    def at(self, fx: float, py: float) -> QColor:
        """The pixel at a fraction of the width, ``py`` device px above the bottom edge
        (negative: below the top edge)."""
        x = int(self.x + fx * self.w)
        y = int(self.y + self.h - 1 - py) if py >= 0 else int(self.y - py)
        return self.img.pixelColor(x, y)

    def band_luminances(self) -> list[float]:
        """Every pixel of the caption band, clear of the rounded corners and the bar."""
        ls = []
        top, bottom = int(self.y + self.h - self.band + 1), int(self.y + self.h - 5 * self.dpr)
        for y in range(top, bottom):
            for x in range(int(self.x + 8 * self.dpr), int(self.x + self.w - 8 * self.dpr)):
                ls.append(luminance(self.img.pixelColor(x, y)))
        return sorted(ls)

    def contrast(self) -> tuple[float, int]:
        """Text against the band: the brightest glyph pixels over the median (the
        background: glyphs cover a small part of the band); and how many glyph pixels."""
        ls = self.band_luminances()
        bg, text = ls[len(ls) // 2], ls[int(len(ls) * 0.995)]
        glyph = sum(1 for v in ls if v > bg + 0.25)
        return (text + 0.05) / (bg + 0.05), glyph


def run_child(tmp_path: Path) -> dict:
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        pytest.skip("the parked-window child needs a display (windows or X11)")
    # the child picks its own platform and RHI (conftest pins this process to offscreen)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("QT_", "QSG_"))}
    grab = tmp_path / "overlay.png"
    res = subprocess.run(
        [sys.executable, "-I", str(CHILD), str(grab)],
        cwd=CHILD.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    rows = [json.loads(ln) for ln in res.stdout.splitlines() if ln.startswith("{")]
    assert rows, f"no row (exit {res.returncode}): {res.stderr[-800:]}"
    out = rows[0]
    assert "error" not in out, out
    if out["api"] != "OpenGL":
        pytest.skip(f"no OpenGL render path here ({out['api']})")
    return out


def test_captions_read_on_light_and_dark_images_and_the_current_item_has_its_own_mark(tmp_path):
    out = run_child(tmp_path)
    img = QImage(out["grab"])
    assert not img.isNull()
    assert out["resident"] >= 3 and out["captions"] == 3, out
    white, black, current = (Tile(img, r, out["dpr"], out["band"]) for r in out["rects"])

    # the scrim covers the band only: above it the image is untouched
    assert near(white.at(0.5, -white.h * 0.2), QColor("#ffffff"), 12)
    assert near(black.at(0.5, -black.h * 0.2), QColor("#000000"), 12)
    assert luminance(white.at(0.95, white.band * 0.5)) < 0.25  # the band is darkened

    # the caption reads on both (WCAG AA for small text: 4.5 : 1)
    for name, tile in (("white", white), ("black", black)):
        ratio, glyph = tile.contrast()
        assert glyph > 40, f"{name}: no caption text in the band ({glyph} glyph pixels)"
        assert ratio >= 4.5, f"{name}: caption contrast {ratio:.2f} : 1"

    # the current item: a bar along its bottom edge in its own colour, and its label
    assert near(current.at(0.5, 0), NOW), current.at(0.5, 0).name()
    assert not near(white.at(0.5, 0), NOW)
    _, plain = white.contrast()
    _, labelled = current.contrast()
    assert labelled > plain * 1.2, (plain, labelled)  # " · now" after the key

    # the selection ring is another mark: on the selected tile's sides, not a bottom bar
    side = black.at(1.5 / black.w, -black.h * 0.5)  # inside the ring's band, mid-height
    assert luminance(side) > 0.02, side.name()  # the ring, not the black image
    assert not near(side, NOW), side.name()
    assert not near(black.at(0.5, 0), NOW)
    # the current item has no ring of its own
    assert not near(current.at(1.5 / current.w, -current.h * 0.5), SELECTION, 60)
