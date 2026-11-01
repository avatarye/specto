"""The walk-through's camera and motion rules (#174, design page 06 motion notes), pure:
hop pans hold when the hop fits, centre it in the safe frame otherwise; pan durations
clamp to 400-900 ms; the overworld shots fit the path above the caption band; a
zoom move interpolates log(zoom); intakers drop dots in the real ratio."""

from __future__ import annotations

import math

import pytest

from specto_app.canvas.walk_camera import (
    dropped,
    ease,
    fit_over,
    hop_fits,
    hop_pan,
    over_of_pan,
    pan_ms,
    pulse_every_ms,
    safe_frame,
    zoom_between,
)

AREA = (1280.0, 742.0)
PAD = 12.0


def test_ease_runs_from_rest_to_rest_through_the_middle():
    assert ease(0) == pytest.approx(0, abs=1e-6)
    assert ease(1) == pytest.approx(1, abs=1e-6)
    assert ease(0.5) == pytest.approx(0.5, abs=1e-3)
    assert ease(0.1) < 0.1 and ease(0.9) > 0.9  # slow in, slow out


def test_pan_durations_clamp_between_400_and_900_ms():
    assert pan_ms(0) == 400
    assert pan_ms(400) == 500
    assert pan_ms(5000) == 900


def test_the_safe_frame_leaves_24_px_on_top_and_the_190_px_band():
    assert safe_frame(*AREA) == (0, 24, 1280, 742 - 24 - 190)


def test_a_hop_that_already_fits_holds_the_camera():
    pan = (0.0, 0.0)
    # two views well inside the safe frame at this pan
    assert hop_pan(pan, AREA, PAD, [(100, 60, 300, 300), (450, 60, 300, 300)]) == pan


def test_a_hop_off_screen_is_centred_in_the_safe_frame():
    pan = (0.0, 0.0)
    hop = [(2000, 100, 300, 200), (2400, 100, 300, 200)]  # union 2000..2700 x 100..300
    x, y = hop_pan(pan, AREA, PAD, hop)
    # the union's centre (2350, 200) lands on the safe frame's centre (640, 24 + 264)
    assert PAD + 2350 - x == pytest.approx(640, abs=1)
    assert PAD + 200 - y == pytest.approx(24 + (742 - 214) / 2, abs=1)


def test_a_tall_view_counts_as_its_top_part():
    tall = [(0, 0, 52, 52), (80, 0, 280, 900)]  # the project node and a tall file tree
    _, y = hop_pan((0.0, 2000.0), AREA, PAD, tall)
    assert PAD + 0 - y == pytest.approx(24 + 16)  # its top 16 px under the safe frame's


def test_a_hop_wider_than_the_frame_shows_its_target():
    wide = [(0, 0, 900, 300), (2600, 0, 900, 300)]  # a long link, the target right
    x, _ = hop_pan((5000.0, 0.0), AREA, PAD, wide)
    assert PAD + 3500 - x == pytest.approx(1280 - 16)  # the target's right side in
    back = [(2600, 0, 900, 300), (0, 0, 900, 300)]  # the target left of its source
    x, _ = hop_pan((5000.0, 0.0), AREA, PAD, back)
    assert PAD + 0 - x == pytest.approx(16)


def test_a_hop_fits_unless_its_link_runs_past_the_frame():
    assert hop_fits(AREA, [(0, 0, 52, 52), (80, 0, 280, 900)])  # tall counts as its top
    assert not hop_fits(AREA, [(0, 0, 900, 300), (2600, 0, 900, 300)])


def test_the_overworld_shot_fits_the_path_above_the_band_and_never_zooms_past_1():
    zoom, px, py = fit_over((0, 0, 2600, 500), AREA)
    assert zoom == pytest.approx((1280 - 96) / 2600)
    assert px >= 0 and px + 2600 * zoom <= 1280
    assert py >= 24 and py + 500 * zoom + 34 <= 742 - 190  # the chips fit under the views
    small, _, _ = fit_over((0, 0, 100, 100), AREA)
    assert small == 1.0


def test_a_zoom_move_interpolates_log_zoom_between_two_shots():
    a = over_of_pan((0.0, 0.0), PAD)
    assert a == (1.0, PAD, PAD)
    b = (0.25, 100.0, 50.0)
    assert zoom_between(a, b, 0, AREA) == pytest.approx(a)
    assert zoom_between(a, b, 1, AREA) == pytest.approx(b)
    z, _, _ = zoom_between(a, b, 0.5, AREA)
    assert z == pytest.approx(math.sqrt(0.25))  # the geometric, not the arithmetic, mean


def test_a_long_wire_spaces_its_dots_so_at_most_12_run():
    assert pulse_every_ms(100) == 180
    assert pulse_every_ms(220 * 12 * 0.5) == pytest.approx(500)  # 6 s of travel


def test_an_intaker_drops_dots_in_the_real_ratio():
    kept = [k for k in range(412) if not dropped(128 / 412, k)]
    assert len(kept) in (128, 129)
    assert not dropped(1 / 412, 0)  # one dot of front.mp4 gets through
    assert all(dropped(1 / 412, k) for k in range(1, 50))
    assert not any(dropped(-1, k) for k in range(20))  # not an intaker: none
