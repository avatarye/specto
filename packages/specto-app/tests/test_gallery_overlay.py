"""Gallery captions overlay the thumbnail (SPEC-6): light text over a dark scrim on the
thumb's bottom band, readable on a white image and a black one alike; the current
(linked) item marked apart from the selection ring (a bar along its bottom edge, and
" · now" after its caption); and no caption, and no scrim, on a thumb too short for one.

The offscreen software renderer draws no gallery tiles, so the pixels come from a child
process on the real platform and the OpenGL scene graph (gallery_overlay_child.py: a tool
window parked off every monitor that never takes the focus, one scenario per run);
skipped where there is no display or no GPU path. In every scenario tile 1 is selected
and tile 2 is the current item."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from specto_app.theme import COLORS
from specto_app.views import gallery_native

CHILD = Path(__file__).with_name("gallery_overlay_child.py")
#: the item's defaults are the theme's tokens (test_gallery_native checks they agree)
MARK = QColor(COLORS["current_mark"])
SELECTION = QColor("#2f6fab")
WHITE, BLACK = QColor("#ffffff"), QColor("#000000")
SUFFIX = " · now"

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

    def side(self) -> QColor:
        """Inside the left edge's ring band, at mid-height."""
        return self.at(1.5 / self.w, -self.h * 0.5)

    def band_luminances(self, top: float = 0.0, bottom: float = 1.0) -> list[float]:
        """The caption band's pixels between two fractions of its height (0 = its top),
        clear of the rounded corners and the current item's bar."""
        y0 = self.y + self.h - self.band
        rows = range(int(y0 + 1 + top * self.band), int(y0 + bottom * self.band - 4 * self.dpr))
        cols = range(int(self.x + 8 * self.dpr), int(self.x + self.w - 8 * self.dpr))
        return sorted(luminance(self.img.pixelColor(x, y)) for y in rows for x in cols)

    def contrast(self, top: float = 0.0, bottom: float = 1.0) -> tuple[float, int]:
        """Text against the band: the brightest glyph pixels over the median (the
        background: glyphs cover a small part of the band); and how many glyph pixels."""
        ls = self.band_luminances(top, bottom)
        bg, text = ls[len(ls) // 2], ls[int(len(ls) * 0.995)]
        glyph = sum(1 for v in ls if v > bg + 0.25)
        return (text + 0.05) / (bg + 0.05), glyph


def draw(tmp_path: Path, scenario: str) -> tuple[dict, list[Tile]]:
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        pytest.skip("the parked-window child needs a display (windows or X11)")
    # the child picks its own platform and RHI (conftest pins this process to offscreen)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("QT_", "QSG_"))}
    grab = tmp_path / f"{scenario}.png"
    res = subprocess.run(
        [sys.executable, "-I", str(CHILD), scenario, str(grab)],
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
    assert out["resident"] >= len(out["rects"]), out
    img = QImage(out["grab"])
    assert not img.isNull()
    return out, [Tile(img, r, out["dpr"], out["band"]) for r in out["rects"]]


def test_a_key_reads_on_light_and_dark_images_and_the_current_item_has_its_own_mark(tmp_path):
    out, (white, black, current) = draw(tmp_path, "key")
    assert out["drawn"] == ["MWMWMW", "MWMWMW", "MWMWMW" + SUFFIX]

    # the scrim covers the band only: above it the image is untouched
    assert near(white.at(0.5, -white.h * 0.2), WHITE, 12)
    assert near(black.at(0.5, -black.h * 0.2), BLACK, 12)
    assert luminance(white.at(0.95, white.band * 0.5)) < 0.25  # the band is darkened

    # the caption reads on both (WCAG AA for small text: 4.5 : 1)
    for name, tile in (("white", white), ("black", black)):
        ratio, glyph = tile.contrast()
        assert glyph > 40, f"{name}: no caption text in the band ({glyph} glyph pixels)"
        assert ratio >= 4.5, f"{name}: caption contrast {ratio:.2f} : 1"

    # the current item: a bar along its bottom edge in its own colour, and the suffix
    assert near(current.at(0.5, 0), MARK), current.at(0.5, 0).name()
    assert not near(white.at(0.5, 0), MARK)
    assert current.contrast()[1] > white.contrast()[1] * 1.2  # " · now" after the key

    # the selection ring is another mark: on the selected tile's sides, not a bottom bar
    assert luminance(black.side()) > 0.02, black.side().name()  # the ring, not the image
    assert not near(black.side(), MARK)
    assert not near(black.at(0.5, 0), MARK)
    assert not near(current.side(), SELECTION, 60)  # the current item has no ring


def test_a_key_and_a_name_read_on_two_lines(tmp_path):
    out, (white, black, _current) = draw(tmp_path, "key_and_name")
    assert out["drawn"] == [
        "MWMWMW\nMWMW.jpg",
        "MWMWMW\nMWMW.jpg",
        "MWMWMW" + SUFFIX + "\nMWMW.jpg",
    ]
    for name, tile in (("white", white), ("black", black)):
        ratio, _glyph = tile.contrast()
        assert ratio >= 4.5, f"{name}: caption contrast {ratio:.2f} : 1"
        # glyphs on both lines: the band's upper half and its lower half
        assert tile.contrast(0.0, 0.5)[1] > 20 and tile.contrast(0.5, 1.0)[1] > 20, name
    assert near(white.at(0.5, -white.h * 0.15), WHITE, 12)  # the taller band, still a band


def test_with_captions_off_only_the_current_items_bar_shows(tmp_path):
    out, (white, _black, current) = draw(tmp_path, "off")
    assert out["band"] == 0 and out["drawn"] == ["", "", ""]
    assert near(white.at(0.5, 6), WHITE, 12)  # no scrim above the bottom edge
    assert near(current.at(0.5, 6), WHITE, 12)
    assert near(current.at(0.5, 0), MARK), current.at(0.5, 0).name()
    assert not near(white.at(0.5, 0), MARK)


def test_a_long_key_elides_and_the_current_items_suffix_stays_whole(tmp_path):
    out, _tiles = draw(tmp_path, "long_key")
    plain, _, current = out["drawn"]
    assert "…" in plain and SUFFIX not in plain
    assert current.endswith(SUFFIX) and "…" in current[: -len(SUFFIX)]
    assert current.startswith("fram") and plain.startswith("frame_")  # the middle elides


def test_a_thumb_too_short_for_a_caption_gets_neither_caption_nor_scrim(tmp_path):
    out, (panorama, black, _current) = draw(tmp_path, "panorama")
    assert panorama.h < out["band"] * out["dpr"] / 0.4  # the band would not fit
    assert out["drawn"][0] == ""
    assert near(panorama.at(0.5, 1), WHITE, 12), panorama.at(0.5, 1).name()  # no scrim
    assert out["drawn"][1] == "MWMWMW"  # its neighbours keep theirs
    assert black.contrast()[1] > 40  # and draw it


def test_small_tiles_drop_the_caption_rather_than_cover_the_image(tmp_path):
    out, (white, _black, current) = draw(tmp_path, "small")
    assert out["band"] > 0  # wide enough for captions in general
    assert white.h * 0.4 < out["band"] * out["dpr"]  # but too short for this band
    assert out["drawn"] == ["", "", ""]
    assert near(white.at(0.5, 2), WHITE, 12), white.at(0.5, 2).name()  # no scrim
    assert near(white.at(0.5, white.h * 0.5), WHITE, 12)
    assert near(current.at(0.5, 0), MARK), current.at(0.5, 0).name()  # the bar still shows
