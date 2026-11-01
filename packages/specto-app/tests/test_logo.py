"""The mark read from its design files (#170): the parser on small hand-made sets (the
body stroked, or a filled outline), and the shipped art (``assets/logo``, the designer's
export) checked for what the app needs: every part present, and eyes that never touch
the S, whichever way it is drawn, wherever the states and the gaze move them."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from specto_app.logo import LogoError, gaze_offset, load_logo, logo, path_segments
from specto_app.prompt.icon_pose import poses

MARK = '<g id="mark" transform="translate(6.24 6.24) scale(0.3952)">{}</g>'


def svg(body: str, size: int = 52, comment: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}">'
        f"<!-- {comment} -->{body}</svg>"
    )


#: a filled outline: a square ring (the outer square clockwise, the hole against it)
RING = "M 20 20 L 80 20 L 80 80 L 20 80 Z M 30 30 L 30 70 L 70 70 L 70 30 Z"


def write_set(
    folder: Path,
    *,
    travel: str = "within dx -6..+6, dy -5..+5 units",
    filled: bool = False,
    fill_rule: str = "",
) -> Path:
    s = (
        f'<path id="s-body" d="{RING}" fill="#3A3630"{fill_rule}/>'
        if filled
        else '<path id="s-body" d="M 80 30 L 20 30 L 20 70 L 80 70" '
        'fill="none" stroke="#3A3630" stroke-width="10" stroke-linecap="round"/>'
    )

    def circles(left: str, right: str, r: int) -> str:
        return (
            f'<circle id="eye-left" cx="{left}" cy="45" r="{r}"/>'
            f'<circle id="eye-right" cx="{right}" cy="45" r="{r}"/>'
        )

    def paths(left: str, right: str, width: int) -> str:
        return (
            f'<path id="eye-left" d="{left}" fill="none" stroke="#000" stroke-width="{width}"/>'
            f'<path id="eye-right" d="{right}" fill="none" stroke="#000" stroke-width="{width}"/>'
        )

    eyes = circles("40", "60", 5)
    wide = circles("39", "61", 7)
    lines = paths("M 35 45 L 45 45", "M 55 45 L 65 45", 2)
    xs = paths("M 36 41 L 44 49 M 44 41 L 36 49", "M 56 41 L 64 49 M 64 41 L 56 49", 3)
    small_body = s if filled else s.replace('"10"', '"12"')
    small = '<g id="mark" transform="translate(0.8 0.8) scale(0.144)">' + small_body + eyes + "</g>"
    files = {
        "s-body.svg": svg(MARK.format(s)),
        "s-16.svg": svg(small, 16),
        "eyes-open.svg": svg(MARK.format(eyes), comment=f"Eyes at rest. {travel}."),
        "eyes-wide.svg": svg(MARK.format(wide)),
        "eyes-blink.svg": svg(MARK.format(lines)),
        "eyes-x.svg": svg(MARK.format(xs)),
    }
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def test_the_parts_come_from_the_files(tmp_path):
    art = load_logo(write_set(tmp_path))
    assert art.body.body.d.startswith("M 80 30") and art.body.body.width == 10
    assert art.body.body.cap == "round" and not art.body.body.filled
    assert (art.body.origin, art.body.scale, art.body.canvas) == (6.24, 0.3952, 52)
    assert [(e.cx, e.cy, e.r) for e in art.open] == [(40, 45, 5), (60, 45, 5)]
    assert [(e.cx, e.r) for e in art.wide] == [(39, 7), (61, 7)]
    assert art.small.body.width == 12 and art.small.scale == 0.144 and art.small.canvas == 16
    assert art.travel == (6, 5)
    assert art.blink_lid == pytest.approx(2 / 10)
    q = art.to_qml()
    assert q["origin"] == pytest.approx(0.12) and q["unit"] == pytest.approx(0.0076)
    assert [(x["cx"], x["cy"]) for x in q["x"]] == [(40, 45), (60, 45)]


def test_a_filled_outline_body_is_read_as_a_fill(tmp_path):
    body = load_logo(write_set(tmp_path, filled=True)).body.body
    assert body.filled and body.width == 0 and body.fill_rule == "nonzero"
    # the ring covers its band and not its hole (nonzero: the hole turns the other way)
    assert body.inside((25, 50)) and not body.inside((50, 50)) and not body.inside((10, 50))
    assert body.distance((25, 50)) == pytest.approx(-5)
    assert body.distance((50, 50)) == pytest.approx(20)
    assert body.distance((10, 50)) == pytest.approx(10)
    q = load_logo(tmp_path).to_qml()["body"]
    assert q["filled"] is True and q["fillRule"] == "nonzero"


def test_an_even_odd_outline_keeps_its_rule(tmp_path):
    body = load_logo(write_set(tmp_path, filled=True, fill_rule=' fill-rule="evenodd"')).body.body
    assert body.fill_rule == "evenodd" and not body.inside((50, 50))


def test_a_stroked_body_measures_from_its_centre_line(tmp_path):
    body = load_logo(write_set(tmp_path)).body.body
    assert body.distance((50, 30)) == pytest.approx(-5)  # on the line: half the width in
    assert body.distance((50, 50)) == pytest.approx(15)


def test_a_missing_part_is_named(tmp_path):
    folder = write_set(tmp_path)
    (folder / "eyes-open.svg").write_text(svg(MARK.format("")), encoding="utf-8")
    with pytest.raises(LogoError, match=r"eyes-open\.svg.*eye-left"):
        load_logo(folder)


def test_eyes_without_a_travel_note_are_refused(tmp_path):
    with pytest.raises(LogoError, match="travel"):
        load_logo(write_set(tmp_path, travel="Eyes at rest."))


def test_the_travel_may_be_stated_as_an_ellipse(tmp_path):
    folder = write_set(
        tmp_path, travel="Move both eyes inside the ELLIPSE with semi-axes (6, 7) units"
    )
    assert load_logo(folder).travel == (6, 7)


def test_path_data_reads_lines_and_curves_absolute_and_relative():
    segs = path_segments("M 10 10 L 20 10 h 5 v 5 C 25 20 30 25 35 25 l -5 0 Z")
    assert segs[0] == ("L", ((10, 10), (20, 10)))
    assert segs[1] == ("L", ((20, 10), (25, 10)))
    assert segs[2] == ("L", ((25, 10), (25, 15)))
    assert segs[3] == ("C", ((25, 15), (25, 20), (30, 25), (35, 25)))
    assert segs[4] == ("L", ((35, 25), (30, 25)))
    assert segs[5] == ("L", ((30, 25), (10, 10)))  # Z closes back to the start


def test_the_gaze_moves_the_eyes_within_the_travel():
    assert gaze_offset(1, 0, (8, 9)) == (8, 0)
    assert gaze_offset(0, -1, (8, 9)) == (0, -9)
    assert gaze_offset(0.5, 0.5, (8, 9)) == (4, 4.5)
    dx, dy = gaze_offset(3, 4, (8, 9))  # beyond the unit circle: its direction (0.6, 0.8)
    assert (dx, dy) == pytest.approx((4.8, 7.2))
    assert gaze_offset(float("nan"), 1, (8, 9)) == (0, 0)


# -- the shipped art ---------------------------------------------------------------------------
def clearance(art, eyes, dx: float, dy: float) -> float:
    """The smallest gap between an eye's edge and the drawn S's edge, in grid units."""
    return min(art.body.body.distance((e.cx + dx, e.cy + dy)) - e.r for e in eyes)


def test_the_shipped_art_has_every_part():
    art = logo()
    assert len(art.open) == len(art.wide) == len(art.x) == len(art.blink) == 2
    assert art.travel[0] > 0 and art.travel[1] > 0
    assert 0 < art.blink_lid < 1


def test_the_eyes_never_touch_the_s_wherever_the_gaze_takes_them():
    art = logo()
    for deg in range(0, 360, 10):
        dx, dy = gaze_offset(math.cos(math.radians(deg)), math.sin(math.radians(deg)))
        assert clearance(art, art.open, dx, dy) > 0, deg


def test_the_eyes_never_touch_the_s_in_the_states_motions():
    art = logo()
    p = poses(art)
    think_dx, think_dy = p["thinking"].offset
    tx = art.travel[0]
    for dx, dy in [(think_dx, think_dy), (-think_dx, think_dy), (-tx, 0), (tx, 0)]:
        assert clearance(art, art.open, dx, dy) > 0, (dx, dy)
    assert clearance(art, art.wide, 0, 0) > 0


def test_the_wide_eyes_never_touch_the_s_within_their_own_travel():
    art = logo()
    for deg in range(0, 360, 10):
        g = (math.cos(math.radians(deg)), math.sin(math.radians(deg)))
        dx, dy = gaze_offset(*g, art.wide_travel)
        assert clearance(art, art.wide, dx, dy) > 0, deg


def test_the_wide_eyes_travel_is_read_when_the_file_gives_one(tmp_path):
    folder = write_set(tmp_path)
    assert load_logo(folder).wide_travel == (0, 0)  # no note: they hold still
    wide = (folder / "eyes-wide.svg").read_text(encoding="utf-8")
    note = "Wide eyes may travel inside an ellipse up to (4.7, 5.5) units"
    (folder / "eyes-wide.svg").write_text(wide.replace("<!--  -->", f"<!-- {note} -->"), "utf-8")
    assert load_logo(folder).wide_travel == (4.7, 5.5)
    assert load_logo(folder).to_qml()["wideTravel"] == [4.7, 5.5]
