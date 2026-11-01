"""Seam 1 of the tile floor (#191; v3 #201, design "08c Tile floor v3"): the cue maths
with no Qt. Expected values are worked from the design's motion spec (frame 08) and the
decisions of 2026-10-25, not recomputed the way the code does."""

from __future__ import annotations

import pytest

from specto_app.canvas.floor import (
    MAX_EFFECTS,
    SELECT_A,
    THINK_PERIOD_S,
    FloorCues,
    band_profile,
    floor_grid,
    tile_side,
)

AGENT = (38.0, 700.0)  # the S icon's centre in area px
VIEWPORT = (-12.0, -12.0, 1280.0, 742.0)  # the canvas area as a canvas rect (pan 0)


def cues(**kw) -> FloorCues:
    c = FloorCues(**kw)
    c.set_agent_anchor(*AGENT)
    return c


def only(effects, group):
    return [e for e in effects if e.group == group]


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 2


# -- the grid: fixed to the screen, sized from the window (#197), full resolution (#201) -------


def test_the_floor_renders_at_full_resolution_with_8_px_cells_of_a_3_px_resting_tile():
    g = floor_grid(1280, 742, 1.0)
    assert g.texel == 1  # device px per texture px: full resolution
    assert g.n == 8 and g.side == 3  # an 8 px cell, a 3 px tile at rest
    assert (g.tex_w, g.tex_h) == (1280, 742)
    assert g.draw_w == 1280 and g.draw_h == 742


def test_a_whole_number_of_cells_spans_the_area_with_the_rest_split_on_both_sides():
    g = floor_grid(1280, 742, 1.0)
    assert (g.cols, g.rows) == (160, 92)  # 160 x 8 = 1280; 92 x 8 = 736 of 742
    assert g.margin_x == 0
    assert g.margin_y == 3  # 6 texels over: 3 above, 3 below
    g = floor_grid(1285, 742, 1.0)
    assert g.cols == 160 and g.margin_x == 2  # 5 texels over: 2 left, 3 right


@pytest.mark.parametrize(
    ("dpr", "texel", "cell"), [(1.0, 1, 8), (1.5, 2, 32 / 3), (2.0, 2, 8), (1.25, 1, 6.4)]
)
def test_a_texture_px_is_round_dpr_device_px_at_any_display_scale(dpr, texel, cell):
    g = floor_grid(1280, 742, dpr)
    assert g.texel == texel
    assert g.n * g.texel / dpr == pytest.approx(cell)  # the cell in area px
    assert g.draw_w * dpr == pytest.approx(g.tex_w * texel)  # every texel the same width
    assert g.draw_w >= 1280 and g.draw_w - 1280 < texel / dpr


def test_the_grid_depends_on_the_window_alone():
    """No camera goes in: a pan or a zoom cannot move it, only a resize."""
    assert floor_grid(1280, 742, 1.0) == floor_grid(1280, 742, 1.0)
    assert floor_grid(1290, 742, 1.0) != floor_grid(1280, 742, 1.0)


@pytest.mark.parametrize(
    ("e", "side"),
    [
        (0.0, 3),  # rest
        (0.1, 3),
        (1 / 6, 4),  # the ladder's steps at 1/6, 1/2 and 5/6
        (0.3, 4),
        (0.5, 5),
        (0.7, 5),
        (0.9, 6),
        (1.0, 6),  # lit: 6 px, a 2 px gap to the next lit tile
        (2.0, 6),  # never over the gap
    ],
)
def test_the_tile_climbs_the_ladder_3_4_5_6_by_whole_pixels(e, side):
    assert tile_side(8, e) == side


def test_at_half_resolution_the_ladder_is_2_4_6_px():
    assert [tile_side(4, e) for e in (0.0, 0.5, 1.0)] == [1, 2, 3]  # texels of 2 px


def test_the_band_is_a_triangle_about_r_of_half_width_w():
    assert band_profile(100, 100, 32, 1.0) == 1.0
    assert band_profile(116, 100, 32, 1.0) == pytest.approx(0.5)
    assert band_profile(84, 100, 32, 0.8) == pytest.approx(0.4)
    assert band_profile(133, 100, 32, 1.0) == 0.0
    # the selection footprint (R 0, w 20, #207; A 0.4, #220) at cell-centre distances from
    # the view's edge: 4 px tiles under the view and one cell out, then rest
    e = [band_profile(d, 0, 20, SELECT_A) for d in (0, 4, 8, 12, 16, 20)]
    assert e == pytest.approx([0.4, 0.32, 0.24, 0.16, 0.08, 0])
    assert [tile_side(8, x) for x in e] == [4, 4, 4, 3, 3, 3]


# -- agent status: screen space at the S icon ---------------------------------------------------


def test_thinking_bands_travel_from_the_icon_and_fade_while_the_agent_thinks():
    c = cues()
    c.agent("thinking", 10.0)
    (band,) = only(c.effects(10.5), "agent")
    assert band.space == "screen" and band.geom == (*AGENT, *AGENT)
    p = ease_out(0.5 / 1.8)
    assert band.R == pytest.approx(24 + 560 * p)
    assert band.A == pytest.approx(1 - p**3)
    assert (band.w, band.colour) == (32, "agent")
    # a band every 1.2 s, each lives 1.8 s: two overlap at 1.3 s
    assert THINK_PERIOD_S == 1.2
    assert len(only(c.effects(11.3), "agent")) == 2


def test_a_thinking_band_reaches_584_px_as_it_goes_out():
    c = cues()
    c.agent("thinking", 0.0)
    c.agent("idle", 0.1)
    (band,) = only(c.effects(1.799), "agent")
    assert band.R == pytest.approx(584, abs=0.1)
    assert band.A == pytest.approx(0, abs=1e-3)


def test_thinking_has_no_cap_and_working_continues_the_same_loop():
    c = cues()
    c.agent("thinking", 0.0)
    c.agent("working", 5.0)  # a tool runs: still thinking for the floor
    c.agent("thinking", 7.0)
    late = only(c.effects(60.0 + 0.5), "agent")  # 60 s in: bands still come (no 20 s cap)
    assert len(late) == 2
    assert pytest.approx(24 + 560 * ease_out(0.5 / 1.8)) in [b.R for b in late]
    assert c.animating(60.5)


def test_bands_already_out_finish_when_thinking_stops_and_then_the_floor_rests():
    c = cues()
    c.agent("thinking", 0.0)
    c.agent("idle", 0.5)
    assert only(c.effects(1.0), "agent")  # the first band finishes its 1.8 s
    assert not c.effects(1.9)
    assert not c.animating(1.9)
    assert c.next_change(1.9) is None


def test_needs_attention_double_band_every_four_seconds_with_a_static_glow_between():
    c = cues()
    c.agent("needs_you", 0.0)
    bands = [e for e in only(c.effects(0.5), "agent") if e.R > 0]
    p0, p1 = ease_out(0.5 / 1.6), ease_out(0.35 / 1.6)  # 150 ms apart
    assert [round(r.R, 3) for r in bands] == [round(24 + 900 * p0, 3), round(24 + 900 * p1, 3)]
    assert bands[0].A == pytest.approx(1 - p0**3)
    assert bands[0].w == 32
    (glow,) = [e for e in only(c.effects(2.0), "agent") if e.R == 0]
    assert (glow.w, glow.A) == (80, pytest.approx(0.35))
    # between the pulses the floor is static, and asks to wake at the next cycle
    assert not c.animating(2.0)
    assert c.next_change(2.0) == pytest.approx(4.0)
    assert c.animating(4.1)


def test_done_is_one_green_band_that_crosses_the_window_with_no_wake():
    c = cues()
    c.turn_done(0.0)
    (band,) = only(c.effects(0.8), "agent")
    assert band.R == pytest.approx(24 + 1400 * 0.75)  # ease-out quad at t / 1.6 = 0.5
    assert band.A == pytest.approx(1 - 0.75**3)
    assert (band.w, band.colour) == (48, "ok")
    assert not hasattr(band, "wake")
    assert not c.effects(1.61)


# -- new views: world space at the view, near and far -----------------------------------------


def test_a_new_view_on_screen_sends_two_bands_250_ms_apart_in_its_chain_colour():
    c = cues()
    where = c.view_created("hehu", (100, 100, 400, 300), "chain_2", VIEWPORT, 0.0)
    assert where == "on"
    a, b = only(c.effects(0.6), "view")
    assert a.space == "world" and a.geom == (100, 100, 500, 400)
    assert a.R == pytest.approx(320 * 0.75)
    assert b.R == pytest.approx(320 * ease_out(0.35 / 1.2))
    assert a.A == pytest.approx(1 - 0.75**3)
    assert (a.w, a.colour) == (32, "chain_2")
    assert not c.effects(1.46)  # 1.45 s in all


def test_a_near_view_off_screen_bands_reach_into_the_window():
    c = cues()
    # 500 px right of the viewport's right edge (1268)
    where = c.view_created("pola", (1768, 100, 300, 200), "chain_0", VIEWPORT, 0.0)
    assert where == "near"
    a, _ = only(c.effects(1.0), "view")
    # T = min(2, 1.2 + 500 / 500) = 2 s; R = (d + 340) * easeOut(t / T)
    assert a.R == pytest.approx(840 * 0.75)
    assert a.A == pytest.approx(1 - 0.75**3)
    assert not c.effects(2.26)


def test_beyond_the_maximum_pulse_distance_a_far_view_gets_edge_pulses_and_the_chip():
    c = cues()
    where = c.view_created("pola", (1268 + 4200, 300, 300, 200), "chain_0", VIEWPORT, 0.0)
    assert where == "far"
    assert not only(c.effects(0.1), "view")[0].space == "world"
    (p0,) = [e for e in only(c.effects(0.5), "view")]
    assert p0.space == "screen"
    # on the line from the window centre to the view, 28 px outside the right edge
    x, y = p0.geom[:2]
    assert x == pytest.approx(1280 + 28)
    assert 371 - 5 < y < 371 + 30
    assert p0.R == pytest.approx(30 + 300 * 0.75) and p0.w == 32
    assert len(only(c.effects(1.3), "view")) == 2  # three pulses 0.6 s apart
    chip = c.chip(1.0)
    assert chip.view == "pola" and chip.text == "4.2k px"
    assert c.chip(4.01) is None  # stays 4 s


def test_the_maximum_pulse_distance_is_fixed_not_a_share_of_the_window():
    c = cues()
    big = (-12.0, -12.0, 3000.0, 2000.0)
    assert c.view_created("a", (3000 - 12 + 1000, 0, 100, 100), "chain_0", big, 0.0) == "near"
    assert c.view_created("b", (3000 - 12 + 1200, 0, 100, 100), "chain_0", big, 0.0) == "far"


def test_a_clicked_chip_goes_away():
    c = cues()
    c.view_created("pola", (9000, 300, 300, 200), "chain_0", VIEWPORT, 0.0)
    assert c.chip_clicked(0.5) == "pola"
    assert c.chip(0.6) is None


# -- the selection footprint ---------------------------------------------------------------------


def test_the_selection_footprint_fades_in_holds_solid_and_fades_out():
    c = cues()
    c.select({"kobi": ((0, 0, 400, 300), "chain_0")}, 0.0)
    (tie,) = only(c.effects(0.1), "tie")
    assert (tie.R, tie.w, tie.space) == (0, 20, "world")  # d = 0 inside: the covered tiles lit
    assert tie.A == pytest.approx(SELECT_A * ease_out(0.5))  # in over 200 ms, ease-out
    assert c.animating(0.1)
    (tie,) = only(c.effects(0.25), "tie")
    assert tie.A == SELECT_A == 0.4  # held at the footprint's amplitude (#220)
    assert not c.animating(0.25)  # static: no redraws
    c.select({}, 1.0)
    out = only(c.effects(1.1), "tie")[0].A
    assert out == pytest.approx(SELECT_A * (1 - 0.5**2))  # out, ease-in
    assert not c.effects(1.21)


def test_the_footprint_hides_while_its_view_is_dragged_and_comes_back_on_the_drop():
    c = cues()
    c.select({"kobi": ((0, 0, 400, 300), "chain_0")}, 0.0)
    c.drag({"kobi"}, 1.0)
    assert not c.effects(1.25)
    c.drag(set(), 2.0)
    c.select({"kobi": ((40, 0, 400, 300), "chain_0")}, 2.0)  # the drop moved it
    (tie,) = only(c.effects(2.25), "tie")
    assert tie.geom == (40, 0, 440, 300) and tie.A == SELECT_A


def test_a_selected_view_that_moves_keeps_its_footprint_without_fading_again():
    c = cues()
    c.select({"kobi": ((0, 0, 400, 300), "chain_0")}, 0.0)
    c.select({"kobi": ((0, 50, 400, 300), "chain_0")}, 1.0)
    (tie,) = only(c.effects(1.0), "tie")
    assert tie.geom == (0, 50, 400, 350) and tie.A == SELECT_A


# -- the budget and idling -----------------------------------------------------------------------


def test_at_most_eight_effects_the_oldest_view_pulse_drops_first():
    c = cues()
    c.agent("needs_you", 0.0)  # two bands and the glow
    c.select({"a": ((0, 0, 10, 10), "chain_0"), "b": ((50, 0, 10, 10), "chain_1")}, 0.0)
    for i in range(3):  # three new views, two bands each, at 0.0, 0.01, 0.02
        c.view_created(f"v{i}", (100 * i, 0, 50, 50), "chain_0", VIEWPORT, i * 0.01)
    fx = c.effects(0.3)
    assert len(fx) == MAX_EFFECTS
    assert len(only(fx, "agent")) == 3 and len(only(fx, "tie")) == 2
    assert {e.view for e in only(fx, "view")} == {"v1", "v2"}  # v0's bands dropped
    assert {e.view for e in only(fx, "view")[:2]} == {"v2"}  # newest first


def test_nothing_live_means_no_effects_no_ticks_and_no_wake_up():
    c = cues()
    assert c.effects(0.0) == []
    assert not c.animating(0.0)
    assert c.next_change(0.0) is None


# -- reduced motion: one 600 ms bump of the same tiles, no travelling band ----------------------


def test_reduced_motion_turns_a_band_into_a_single_bump_in_place():
    c = cues(reduced_motion=True)
    c.view_created("hehu", (100, 100, 400, 300), "chain_2", VIEWPORT, 0.0)
    (fade,) = only(c.effects(0.3), "view")
    assert fade.R == 0 and fade.A == pytest.approx(1.0)  # the peak at mid-bump
    assert only(c.effects(0.1), "view")[0].R == 0
    assert not c.effects(0.61)


def test_reduced_motion_thinking_is_a_held_glow_not_a_loop():
    c = cues(reduced_motion=True)
    c.agent("thinking", 0.0)
    (glow,) = only(c.effects(5.0), "agent")
    assert glow.R == 0 and glow.A == pytest.approx(0.35)
    assert not c.animating(5.0)
