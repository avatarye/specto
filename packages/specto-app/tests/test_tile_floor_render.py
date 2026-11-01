"""Seam 2 of the tile floor (#191, #197; v3 #201): ``TileFloor.qml`` and its shader
rendered with OpenGL offscreen (a child process: the tests' own Qt Quick is the software
one, which runs no shaders). Colours are the theme's: bg #f4efe6, bg_tint #ece6da,
chain_0 #4a7fb5."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

CHILD = Path(__file__).resolve().parent / "tile_floor_child.py"
BG, TINT, CHAIN_0 = [244, 239, 230], [236, 230, 218], [74, 127, 181]


@pytest.fixture(scope="module")
def out() -> dict:
    r = subprocess.run([sys.executable, str(CHILD)], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("RESULT "))
    return json.loads(line[len("RESULT ") :])


def test_the_floor_renders_on_the_gpu(out):
    assert out["gpu"] is True


def test_resting_tiles_are_square_bg_tint_3_px_at_offset_2_of_an_8_px_cell(out):
    rest = out["rest"]
    assert rest["tile"] == TINT
    assert rest["corner"] == TINT  # square: the corner pixel is as solid as the middle
    assert rest["tile_edge"] == TINT
    assert rest["gap"] == BG and rest["gap_corner"] == BG
    assert rest["gap_before"] == BG  # 2 px before it: 5 px clear between resting tiles


def test_an_idle_floor_asks_for_no_frame(out):
    assert out["idle_requests"] == 0
    assert out["idle_ticking"] is False


def test_the_floor_stays_still_while_the_canvas_pans_and_zooms(out):
    """Fixed to the screen (#197): a slow pan and a zoom with no cue on a view change no
    uniform, and every frame equals the first to the bit."""
    assert out["pan_uniform_changes"] == 0
    assert out["pan_max_diff"] == 0


def test_a_band_at_its_crest_grows_the_tiles_to_6_px_in_the_agent_colour(out):
    band = out["band"]
    r, g, b = band["tile"]
    assert r > g > b and r - b > 60  # agent orange (#b86a2b), e 0.97: nearly solid
    assert band["grown_left"] == band["tile"] and band["grown_right"] == band["tile"]
    assert band["gap"] == BG and band["gap_right"] == BG  # the 2 px gap between lit tiles
    assert band["gap_left"] == BG
    assert band["rest_far"] == TINT  # 40 px behind the crest the band is gone
    assert out["band_ticking"] is True


def test_the_selection_footprint_tints_the_covered_tiles_at_40_percent_and_falls_off_around(
    out,
):
    tie = out["tie"]
    r, _, b = tie["near"]
    assert b > r + 8 and r < TINT[0] - 20  # one cell out, e 0.24: a chain_0 tint
    assert tie["far"] == TINT  # tiles away from the view rest
    # under the view (#220): 40 % of the way from bg_tint to chain_0, a 4 px tile
    want = [round(t + (c - t) * 0.4) for t, c in zip(TINT, CHAIN_0, strict=True)]
    assert all(abs(g - w) <= 2 for g, w in zip(tie["inside_tile"], want, strict=True)), tie
    assert tie["inside_gap"] == BG
    assert tie["widths_to_edge"] == [3, 3, 3, 3, 4]  # d 40..16 rest; 8 climbs to 4 (#207, #220)
    # the footprint follows its view across a pan: the camera goes in while it shows
    assert out["tie_panned"]["near"] == tie["near"]
    left = out["tie_panned"]["left_behind"]
    assert sum(abs(a - b) for a, b in zip(left, TINT, strict=True)) < 12
    assert out["tie_pan_uniform_changes"] >= 1
    assert out["tie_ticking_after_fade"] is False
    assert out["tie_idle_requests"] == 0  # a held tie is static


def test_the_wiring_layer_dims_the_tiles_to_30_percent(out):
    assert out["dim"]["dim"] == pytest.approx(0.3)
    got = out["dim"]["tile"]
    want = [round(b + (t - b) * 0.3) for b, t in zip(BG, TINT, strict=True)]
    assert all(abs(g - w) <= 2 for g, w in zip(got, want, strict=True))
