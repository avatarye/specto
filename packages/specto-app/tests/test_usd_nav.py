"""Seam 1 (#138, #151): the usd view's camera navigation model, Unreal Editor's
perspective viewport (the table in ``usd/nav.py``), one test per gesture: LMB turns and
moves on the ground, RMB looks and (held) flies with W A S D Q E and sets the speed with
the wheel, MMB or LMB+RMB pans, the wheel dollies, Alt orbits or dollies around a pivot.
The first look and World origin are the 3d view's origin look, F frames bounds keeping
the viewing direction, and the ``camera`` port value has the 3d view's shape and frame,
so a usd view and a 3d view share a camera."""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from specto_app.usd import nav as nav_mod
from specto_app.usd.nav import CameraState, Nav, gf_frustum, usd_camera
from specto_app.views import scene3d_view
from specto_app.views.scene3d_view import initial_camera
from specto_core.links import CameraValue

BOUNDS = ((-4.0, -1.0, -3.0), (6.0, 2.0, 5.0))


def close(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def same_camera(a: dict, b: dict, tol=1e-9) -> bool:
    """Two camera port values agree, key by key."""
    assert set(a) == set(b)
    return all(
        close(a[k], b[k], tol) if isinstance(a[k], list | tuple) else abs(a[k] - b[k]) <= tol
        for k in a
    )


def test_first_look_is_the_3d_views_origin_look():
    nav = Nav(BOUNDS)
    assert same_camera(nav.camera_value(), initial_camera([{"bounds": BOUNDS}]))


MAIN_JS = Path(__file__).parents[2] / "specto-web" / "web" / "3d" / "main.js"


def js_number(name: str) -> float:
    """A constant as the 3d page writes it: ``NAME = 0.02`` or ``camera.name = 0.6;``."""
    m = re.search(rf"\b{re.escape(name)}\s*=\s*(-?[\d.]+)\s*[,;]", MAIN_JS.read_text("utf-8"))
    assert m, f"{name} not found in {MAIN_JS}"
    return float(m.group(1))


def test_constants_are_the_3d_pages():
    # the 3d page's Babylon camera inputs (wheel, pan, inertia, angular sensibility) left
    # it for web/viewnav.js's Unreal Editor controls (#152); #151 pins the usd view's
    # rates to that module's
    assert nav_mod.SPEED_MIN == js_number("SPEED_MIN")
    assert nav_mod.SPEED_MAX == js_number("SPEED_MAX")
    assert nav_mod.LOWER_RADIUS_LIMIT == js_number("camera.lowerRadiusLimit")
    assert nav_mod.DEFAULT_FOV_Y == scene3d_view.DEFAULT_FOV_Y
    assert nav_mod.FIRST_LOOK == scene3d_view.FIRST_LOOK
    assert nav_mod.DEFAULT_RADIUS == scene3d_view.DEFAULT_RADIUS


@pytest.mark.parametrize(
    ("asked", "speed"),
    [
        (1.0, 1.0),
        (0.5, 0.5),
        (0.01, 0.125),
        (100, 8.0),
        (0, 1.0),
        (-2, 1.0),
        ("x", 1.0),
        (None, 1.0),
    ],
)
def test_camera_speed_is_clamped_to_an_eighth_to_eight(asked, speed):
    nav = Nav(BOUNDS)
    nav.camera_speed = asked
    assert nav.camera_speed == speed


def test_frame_all_fits_the_bounds_and_keeps_the_viewing_direction():
    nav = Nav(BOUNDS)
    nav.apply_camera_value({"position": [1.0, 10.0, 1.0], "target": [1.0, 0.0, -9.0]})
    nav.frame_all()
    v = nav.camera_value()
    # the 3d page's frameBox: the bounds' centre, half the diagonal, back by r / sin(fov/2) x 1.05
    radius = math.sqrt(10**2 + 3**2 + 8**2) / 2
    distance = radius / math.sin(0.4) * 1.05
    s = 1 / math.sqrt(2)
    assert close(v["target"], (1.0, 0.5, 1.0))
    assert close(v["position"], (1.0, 0.5 + s * distance, 1.0 + s * distance))
    assert v["near"] == pytest.approx(radius / 5000)  # max(0.001, r / 5000)
    assert v["far"] == pytest.approx(radius * 50)  # max(100, r x 50)


def test_frame_all_of_a_big_world_widens_the_clip_range():
    nav = Nav(((-500.0, -500.0, -500.0), (500.0, 500.0, 500.0)))
    nav.frame_all()
    radius = math.sqrt(3) * 500
    assert nav.camera_value()["near"] == pytest.approx(radius / 5000)
    assert nav.camera_value()["far"] == pytest.approx(radius * 50)


HALF_DIAGONAL = math.sqrt(10**2 + 3**2 + 8**2) / 2  # BOUNDS' scene radius
U = HALF_DIAGONAL  # the table's u (reach x camera speed) at speed 1
ROTATE, WALK, PAN, DOLLY = nav_mod.ROTATE, nav_mod.WALK, nav_mod.PAN, nav_mod.DOLLY


VIEWNAV_JS = Path(__file__).parents[2] / "specto-web" / "web" / "viewnav.js"
SHARED_RATES = ("ROTATE", "WALK", "PAN", "DOLLY", "WHEEL", "FLY", "SHIFT", "SPEED_STEP")


@pytest.mark.parametrize("name", [*SHARED_RATES, "CLICK_SLOP", "SPEED_MIN", "SPEED_MAX"])
def test_the_rates_are_viewnav_js(name):
    # the 3d view's web/viewnav.js (#152) moves by the same table (until #152 lands here,
    # the table's numbers)
    if not VIEWNAV_JS.is_file():
        table = dict(zip(SHARED_RATES, (0.004, 0.01, 0.004, 0.01, 0.1, 0.5, 4, 0.25), strict=True))
        want = {**table, "CLICK_SLOP": 4, "SPEED_MIN": -3, "SPEED_MAX": 3}[name]
    else:
        m = re.search(rf"\b{name}\s*=\s*(-?[\d.]+)\s*[,;]", VIEWNAV_JS.read_text("utf-8"))
        assert m, f"{name} not found in {VIEWNAV_JS}"
        want = float(m.group(1))
    assert getattr(nav_mod, name) == want
    assert nav_mod.PITCH_LIMIT == math.pi / 2 - 0.01


def looking_down_minus_z() -> Nav:
    """A camera at (0, 0, 10) looking at the origin: forward is -Z, right is +X, up is +Y."""
    nav = Nav(BOUNDS)
    nav.apply_camera_value({"position": [0.0, 0.0, 10.0], "target": [0.0, 0.0, 0.0]})
    return nav


def forward_of(nav: Nav) -> list[float]:
    v = nav.camera_value()
    d = [t - p for t, p in zip(v["target"], v["position"], strict=True)]
    n = math.hypot(*d)
    return [c / n for c in d]


def drag(nav: Nav, buttons: str, dx: float, dy: float, alt: bool = False) -> None:
    """Press ``buttons`` (l r m, in order), move once, let go."""
    names = {"l": "left", "r": "right", "m": "middle"}
    for b in buttons:
        nav.press(names[b], alt)
    nav.drag(dx, dy)
    for b in buttons:
        nav.release(names[b])


def fly(nav: Nav, keys: str, seconds: float, hz: int = 60) -> None:
    """Hold RMB and ``keys`` for ``seconds``, ticking at ``hz``."""
    nav.press("right")
    for k in keys:
        nav.set_key(k, True)
    for _ in range(round(seconds * hz)):
        nav.tick(1 / hz)
    for k in keys:
        nav.set_key(k, False)
    nav.release("right")


# -- the gestures (#151: Unreal Editor's perspective viewport) ----------------------------------
@pytest.mark.parametrize(
    ("buttons", "alt", "gesture"),
    [
        ("l", False, "walk"),
        ("r", False, "look"),
        ("m", False, "pan"),
        ("lr", False, "pan"),
        ("rl", False, "pan"),
        ("l", True, "orbit"),
        ("r", True, "dolly"),
        ("m", True, "pan"),
        ("rl", True, "orbit"),
        ("lm", True, "pan"),
        ("", False, None),
    ],
)
def test_the_held_buttons_and_alt_choose_the_gesture(buttons, alt, gesture):
    nav = Nav(BOUNDS)
    for b in buttons:
        nav.press({"l": "left", "r": "right", "m": "middle"}[b], alt)
    assert nav.gesture == gesture


def test_alt_is_read_as_the_first_button_goes_down_and_holds_until_the_last_comes_up():
    nav = Nav(BOUNDS)
    nav.press("left", alt=True)
    nav.press("right", alt=False)  # a chord: still an Alt gesture
    nav.release("right")
    assert nav.gesture == "orbit"
    nav.release("left")
    nav.press("left")
    assert nav.gesture == "walk"


def test_a_left_drag_turns_and_walks_along_the_ground():
    nav = looking_down_minus_z()
    drag(nav, "l", 60, 0)  # right: the camera turns right, in place
    assert nav.yaw == pytest.approx(60 * ROTATE)
    assert close(nav.camera_value()["position"], (0.0, 0.0, 10.0))
    nav = looking_down_minus_z()
    drag(nav, "l", 0, -40)  # up: forward, the focus point along
    assert close(nav.camera_value()["position"], (0.0, 0.0, 10 - 40 * WALK * U), 1e-9)
    assert close(nav.camera_value()["target"], (0.0, 0.0, -40 * WALK * U), 1e-9)


def test_the_left_drag_moves_on_the_ground_whatever_the_pitch():
    nav = Nav(BOUNDS)
    nav.apply_camera_value({"position": [0.0, 5.0, 10.0], "target": [0.0, 0.0, 0.0]})
    drag(nav, "l", 0, -40)
    p = nav.camera_value()["position"]
    assert p[1] == pytest.approx(5.0)  # the height stays
    assert close(p, (0.0, 5.0, 10 - 40 * WALK * U), 1e-9)


def test_a_right_drag_looks_around_from_where_the_eye_is():
    nav = looking_down_minus_z()
    drag(nav, "r", 60, -30)  # right and up: turn right, look up
    assert nav.yaw == pytest.approx(60 * ROTATE)
    assert nav.pitch == pytest.approx(30 * ROTATE)
    assert close(nav.camera_value()["position"], (0.0, 0.0, 10.0))
    f = forward_of(nav)
    assert f[0] > 0 and f[1] > 0


def test_looking_never_goes_past_straight_up_or_down():
    nav = looking_down_minus_z()
    drag(nav, "r", 0, -100000)
    assert nav.pitch == pytest.approx(nav_mod.PITCH_LIMIT)
    drag(nav, "r", 0, 100000)
    assert nav.pitch == pytest.approx(-nav_mod.PITCH_LIMIT)


@pytest.mark.parametrize(("buttons", "alt"), [("m", False), ("lr", False), ("m", True)])
@pytest.mark.parametrize("speed", [1.0, 2.0, 0.125])
def test_middle_or_both_buttons_pan_with_the_cursor(buttons, alt, speed):
    nav = looking_down_minus_z()
    nav.camera_speed = speed
    drag(nav, buttons, 40, -40, alt)  # right and up: the camera goes right and up
    moved = 40 * PAN * U * speed
    assert close(nav.camera_value()["position"], (moved, moved, 10.0), 1e-9)
    assert close(nav.camera_value()["target"], (moved, moved, 0.0), 1e-9)


def test_a_pan_moves_up_the_screen_not_the_world():
    # screen up = right x forward: looking down 45 degrees it leans away from the eye
    nav = Nav(BOUNDS)
    nav.apply_camera_value({"position": [0.0, 10.0, 10.0], "target": [0.0, 0.0, 0.0]})
    drag(nav, "m", 0, -100)
    d = 100 * PAN * U / math.sqrt(2)
    assert close(nav.camera_value()["position"], (0.0, 10 + d, 10 - d), 1e-9)


@pytest.mark.parametrize("speed", [1.0, 4.0])
def test_the_wheel_dollies_along_the_view(speed):
    nav = looking_down_minus_z()
    nav.camera_speed = speed
    nav.wheel(1)  # a notch away from the user: forward
    d = nav_mod.WHEEL * U * speed
    assert close(nav.camera_value()["position"], (0.0, 0.0, 10 - d), 1e-9)
    assert close(nav.camera_value()["target"], (0.0, 0.0, -d), 1e-9)  # eye and target together
    nav.wheel(-2)
    assert close(nav.camera_value()["position"], (0.0, 0.0, 10 + d), 1e-9)


def test_a_qt_wheel_notch_is_one():
    assert nav_mod.QT_NOTCH == 120


def test_the_wheel_with_the_right_button_held_sets_the_camera_speed():
    nav = looking_down_minus_z()
    nav.press("right")
    nav.wheel(4)
    assert nav.camera_speed == pytest.approx(2.0)  # log2 + 4 x 0.25
    nav.wheel(-12)
    assert nav.camera_speed == pytest.approx(0.25)
    nav.wheel(100)
    assert nav.camera_speed == 8.0  # the slider's range
    assert close(nav.camera_value()["position"], (0.0, 0.0, 10.0))  # no dolly


def test_alt_left_orbits_the_focus_point_without_a_pivot():
    nav = looking_down_minus_z()
    drag(nav, "l", 90, 0, alt=True)
    v = nav.camera_value()
    assert math.dist(v["position"], (0.0, 0.0, 0.0)) == pytest.approx(10.0)
    assert close(v["target"], (0.0, 0.0, 0.0), 1e-9)  # still looking at it
    assert v["position"][0] < 0  # turned right: the eye swung left around it


def test_alt_left_orbits_a_pivot_and_keeps_it_where_it_is_on_screen():
    nav = looking_down_minus_z()
    nav.press("left", alt=True)
    nav.set_pivot((3.0, 1.0, -2.0))  # the point picked under the press (stage frame, Y-up)

    def seen(n: Nav) -> tuple[float, ...]:
        forward, right, up = n._axes()
        rel = [p - e for p, e in zip((3.0, 1.0, -2.0), n.position, strict=True)]
        axes = (right, up, forward)
        return tuple(sum(a * b for a, b in zip(rel, axis, strict=True)) for axis in axes)

    before = seen(nav)
    nav.drag(80, 25)
    assert seen(nav) == pytest.approx(before)
    assert nav.yaw == pytest.approx(80 * ROTATE) and nav.pitch == pytest.approx(-25 * ROTATE)


def test_the_pivot_comes_in_the_stages_frame():
    nav = Nav(BOUNDS, up_axis="Z")
    nav.press("left", alt=True)
    nav.set_pivot((1.0, 2.0, 3.0))
    assert close(nav.pivot, (1.0, 3.0, -2.0))  # the nav frame's (x, z, -y)


def test_a_pivot_needs_an_orbit_on():
    nav = looking_down_minus_z()
    nav.set_pivot((5.0, 5.0, 5.0))
    nav.press("left")
    nav.set_pivot((5.0, 5.0, 5.0))
    nav.release("left")
    nav.press("right", alt=True)  # Alt+RMB dollies: no pivot
    nav.set_pivot((5.0, 5.0, 5.0))
    assert close(nav.pivot, nav.target)
    nav.release("right")
    nav.press("left", alt=True)  # a new orbit starts at the focus point
    assert close(nav.pivot, nav.target)


def test_alt_right_dollies_along_the_view():
    nav = looking_down_minus_z()
    drag(nav, "r", 100, 0, alt=True)  # right: forward
    d = 100 * DOLLY * U
    assert close(nav.camera_value()["position"], (0.0, 0.0, 10 - d), 1e-9)
    assert close(nav.camera_value()["target"], (0.0, 0.0, -d), 1e-9)
    drag(nav, "r", 0, 100, alt=True)  # down: back
    assert close(nav.camera_value()["position"], (0.0, 0.0, 10.0), 1e-9)


# -- orbit mode (#39) ---------------------------------------------------------------------------
def orbiting(anchor: tuple[float, float, float] | None = (0.0, 0.0, 0.0)) -> Nav:
    """The camera of ``looking_down_minus_z`` in orbit mode, anchored (the selection's
    centre) at ``anchor``, the origin it looks at by default."""
    nav = looking_down_minus_z()
    nav.set_mode("orbit")
    nav.set_anchor(anchor)
    return nav


@pytest.mark.parametrize(
    ("buttons", "alt", "gesture"),
    [
        ("l", False, "orbit"),
        ("r", False, "zoom"),
        ("m", False, "pan"),
        ("lr", False, "pan"),
        ("rl", False, "pan"),
        ("l", True, "orbit"),  # Alt changes nothing
        ("r", True, "zoom"),
        ("m", True, "pan"),
        ("", False, None),
    ],
)
def test_orbit_mode_chooses_its_gestures_with_or_without_alt(buttons, alt, gesture):
    nav = orbiting()
    for b in buttons:
        nav.press({"l": "left", "r": "right", "m": "middle"}[b], alt)
    assert nav.gesture == gesture
    assert nav.pivoting == (gesture in ("orbit", "zoom"))


def test_switching_the_mode_keeps_the_camera_where_it_is():
    nav = looking_down_minus_z()
    drag(nav, "r", 70, -30)  # looked somewhere
    nav.take_change()
    before = nav.camera_value()
    nav.set_mode("orbit")
    assert nav.mode == "orbit" and same_camera(nav.camera_value(), before)
    assert nav.take_change() is None  # nothing for the renderer, no cut
    nav.set_mode("free")
    assert nav.mode == "free" and same_camera(nav.camera_value(), before)
    nav.set_mode("sideways")  # not a mode: free
    assert nav.mode == "free"


def test_switching_the_mode_lets_go_of_buttons_and_keys():
    nav = looking_down_minus_z()
    nav.press("right")
    nav.set_key("w", True)
    nav.set_mode("orbit")
    assert nav.gesture is None and not nav.moving


def test_in_orbit_mode_a_left_drag_turns_around_the_anchor_about_world_up():
    nav = orbiting()
    drag(nav, "l", 90, 0)  # right: a yaw about world up through the anchor
    v = nav.camera_value()
    assert math.dist(v["position"], (0.0, 0.0, 0.0)) == pytest.approx(10.0)  # the same reach
    assert v["position"][1] == pytest.approx(0.0)  # no climb: a turn about Y
    assert v["position"][0] < 0  # turned right: the eye swung left around it
    assert close(v["target"], (0.0, 0.0, 0.0), 1e-9)  # still looking at the anchor
    assert nav.yaw == pytest.approx(90 * ROTATE) and nav.pitch == 0
    drag(nav, "l", 0, -50)  # up: the eye swings under the anchor, looking up at it
    v = nav.camera_value()
    assert math.dist(v["position"], (0.0, 0.0, 0.0)) == pytest.approx(10.0)
    assert v["position"][1] < 0 and nav.pitch == pytest.approx(50 * ROTATE)
    assert close(v["target"], (0.0, 0.0, 0.0), 1e-9)
    assert close(nav.state().up, (0.0, 1.0, 0.0))  # never a roll


def test_an_orbit_stops_short_of_straight_up_and_down():
    nav = orbiting()
    drag(nav, "l", 0, 5000)
    assert nav.pitch == -nav_mod.PITCH_LIMIT
    assert math.dist(nav.camera_value()["position"], (0.0, 0.0, 0.0)) == pytest.approx(10.0)
    drag(nav, "l", 0, -9000)
    assert nav.pitch == nav_mod.PITCH_LIMIT


def test_an_orbit_keeps_an_off_axis_anchor_where_it_is_on_screen():
    nav = orbiting((3.0, 1.0, -2.0))

    def seen(n: Nav) -> tuple[float, ...]:
        forward, right, up = n._axes()
        rel = [p - e for p, e in zip((3.0, 1.0, -2.0), n.position, strict=True)]
        axes = (right, up, forward)
        return tuple(sum(a * b for a, b in zip(rel, axis, strict=True)) for axis in axes)

    before = seen(nav)
    drag(nav, "l", 80, 25)
    assert seen(nav) == pytest.approx(before)
    # the focus point sits at the anchor's depth, so the camera port's target is there
    assert nav.focus == pytest.approx(before[2])


def test_the_orbit_pivot_is_the_anchor_else_the_press_point_else_the_focus_point():
    nav = orbiting(None)  # nothing selected
    assert close(nav.pivot, nav.target)
    nav.press("left")
    nav.set_pivot((2.0, 0.0, 0.0))  # the point under the press, from the source
    assert close(nav.pivot, (2.0, 0.0, 0.0))
    nav.release("left")
    assert close(nav.pivot, nav.target)  # gone with the gesture
    nav.set_anchor((0.0, 0.0, 4.0))  # a selection: its centre stands
    assert close(nav.pivot, (0.0, 0.0, 4.0))
    nav.press("right")  # a dolly pivots too: the host asks for a point only without one
    nav.set_pivot((2.0, 0.0, 0.0))
    assert close(nav.pivot, (2.0, 0.0, 0.0))  # the gesture's point wins while it lasts
    nav.release("right")
    assert close(nav.pivot, (0.0, 0.0, 4.0))
    nav.set_anchor(None)
    assert close(nav.pivot, nav.target)


def test_the_anchor_comes_in_the_stages_frame_and_the_free_camera_ignores_it():
    nav = Nav(BOUNDS, up_axis="Z")
    nav.set_mode("orbit")
    nav.set_anchor((1.0, 2.0, 3.0))
    assert close(nav.pivot, (1.0, 3.0, -2.0))  # the nav frame's (x, z, -y)
    nav.set_mode("free")
    assert close(nav.pivot, nav.target)
    nav.press("left", alt=True)  # the free camera's orbit starts at the focus point
    assert close(nav.pivot, nav.target)


def test_in_orbit_mode_the_right_drag_dollies_toward_the_pivot_and_stops_at_it():
    nav = orbiting()
    drag(nav, "r", 100, 0)  # right: closer
    d = 100 * DOLLY * U
    assert close(nav.camera_value()["position"], (0.0, 0.0, 10 - d), 1e-9)
    assert close(nav.camera_value()["target"], (0.0, 0.0, 0.0), 1e-9)  # the focus follows
    drag(nav, "r", 0, 100)  # down: back
    assert close(nav.camera_value()["position"], (0.0, 0.0, 10.0), 1e-9)
    drag(nav, "r", 0, -100000)  # up, far: never past the pivot
    assert close(nav.camera_value()["position"], (0.0, 0.0, nav_mod.LOWER_RADIUS_LIMIT), 1e-9)
    drag(nav, "r", 0, 100)  # and back out again, along the same line
    assert close(nav.camera_value()["position"], (0.0, 0.0, 100 * DOLLY * U + 0.01), 1e-9)


def test_in_orbit_mode_the_wheel_dollies_toward_the_pivot():
    nav = orbiting((3.0, 0.0, 0.0))  # off to the right of the view
    nav.wheel(2)  # away: closer, along the line to the anchor, not the view
    reach = math.dist((0.0, 0.0, 10.0), (3.0, 0.0, 0.0))
    got = 2 * nav_mod.WHEEL * U
    p = nav.camera_value()["position"]
    assert math.dist(p, (3.0, 0.0, 0.0)) == pytest.approx(reach - got)
    assert p[0] > 0 and nav.yaw == 0  # moved toward it, without turning
    assert nav.focus == pytest.approx(10 - 10 * got / reach)  # the anchor's depth
    nav.wheel(-2)
    assert close(nav.camera_value()["position"], (0.0, 0.0, 10.0), 1e-9)
    nav.wheel(1000)
    assert math.dist(nav.camera_value()["position"], (3.0, 0.0, 0.0)) == pytest.approx(
        nav_mod.LOWER_RADIUS_LIMIT
    )


def test_orbit_mode_pans_as_the_free_camera_and_flies_nothing():
    nav = orbiting()
    drag(nav, "m", 100, 0)
    free = looking_down_minus_z()
    drag(free, "m", 100, 0)
    assert same_camera(nav.camera_value(), free.camera_value())
    nav.press("right")  # RMB: a dolly, not a look
    nav.set_key("w", True)
    assert not nav.flying and not nav.moving and nav.tick(1 / 60) is False
    nav.wheel(4)  # and the wheel dollies rather than setting the speed
    assert nav.camera_speed == 1.0
    assert nav.camera_value()["position"][2] < 10.0
    nav.release("right")


def test_a_new_stage_drops_the_anchor_but_keeps_the_mode():
    nav = orbiting((1.0, 1.0, 1.0))
    nav.load(BOUNDS, "Y")
    assert nav.mode == "orbit" and close(nav.pivot, nav.target)


@pytest.mark.parametrize(
    ("keys", "step"),
    [
        ("w", (0, 0, -1)),
        ("s", (0, 0, 1)),
        ("a", (-1, 0, 0)),
        ("d", (1, 0, 0)),
        ("q", (0, -1, 0)),
        ("e", (0, 1, 0)),
        ("wd", (1, 0, -1)),
    ],
)
def test_right_button_fly_moves_at_half_the_scene_radius_per_second(keys, step):
    nav = looking_down_minus_z()
    fly(nav, keys, 1.0)
    d = HALF_DIAGONAL * 0.5
    assert close(nav.camera_value()["position"], [step[0] * d, step[1] * d, 10 + step[2] * d], 1e-6)


def test_w_flies_where_the_camera_looks_and_q_e_along_world_up():
    nav = Nav(BOUNDS)
    nav.apply_camera_value({"position": [0.0, 10.0, 10.0], "target": [0.0, 0.0, 0.0]})
    fly(nav, "w", 1.0)
    d = HALF_DIAGONAL * 0.5 / math.sqrt(2)
    assert close(nav.camera_value()["position"], (0.0, 10 - d, 10 - d), 1e-6)
    before = nav.camera_value()["position"]
    fly(nav, "e", 1.0)
    after = nav.camera_value()["position"]
    assert close(after, (before[0], before[1] + HALF_DIAGONAL * 0.5, before[2]), 1e-6)


def test_fly_keys_do_nothing_without_the_right_button():
    nav = looking_down_minus_z()
    nav.set_key("w", True)
    assert not nav.moving
    assert not nav.tick(1 / 60)
    nav.press("left")
    assert not nav.moving  # the left button does not fly
    nav.release("left")
    nav.press("right", alt=True)
    assert not nav.moving  # nor an Alt gesture


def test_flying_is_held_state_ticked_per_frame():
    # a key down before the right button flies once it is held, until either comes up
    nav = looking_down_minus_z()
    nav.set_key("w", True)
    nav.press("right")
    assert nav.moving
    for _ in range(30):
        assert nav.tick(1 / 60)
    assert nav.camera_value()["position"][2] == pytest.approx(10 - HALF_DIAGONAL * 0.25)
    nav.release("right")
    assert not nav.moving and not nav.tick(1 / 60)
    nav.press("right")
    nav.set_key("w", False)
    assert not nav.moving
    nav.set_key("shift", True)
    assert not nav.moving  # Shift alone flies nowhere


def test_shift_flies_four_times_faster_and_camera_speed_scales_it():
    nav = looking_down_minus_z()
    nav.camera_speed = 0.5
    nav.set_key("shift", True)
    fly(nav, "w", 1.0)
    assert nav.camera_value()["position"][2] == pytest.approx(10 - HALF_DIAGONAL * 0.5 * 4 * 0.5)


def test_a_long_frame_flies_as_far_as_its_time():
    nav = looking_down_minus_z()
    nav.press("right")
    nav.set_key("w", True)
    nav.tick(2.0)
    assert nav.camera_value()["position"][2] == pytest.approx(10 - HALF_DIAGONAL * 0.5 * 2)


def test_without_bounds_the_scene_radius_is_ten():
    nav = Nav()
    nav.apply_camera_value({"position": [0.0, 0.0, 10.0], "target": [0.0, 0.0, 0.0]})
    fly(nav, "w", 1.0)
    assert nav.camera_value()["position"][2] == pytest.approx(5.0)


def test_losing_the_focus_lets_go_of_keys_and_buttons():
    nav = looking_down_minus_z()
    nav.press("right")
    nav.set_key("w", True)
    nav.release_keys()
    assert not nav.moving and nav.gesture is None


# -- the stage's frame and the camera port ------------------------------------------------------
# A Z-up stage is seen as the 3d view sees z-up data: its root turned -90 degrees about X,
# so the stage point (x, y, z) sits at (x, z, -y) in the 3d view's (Babylon's) frame.


def test_a_z_up_stages_first_look_is_the_3d_views_for_the_same_data():
    nav = Nav(BOUNDS, up_axis="Z")
    assert same_camera(nav.camera_value(), initial_camera([{"bounds": BOUNDS}]))
    state = nav.state()
    assert state.up == (0.0, 0.0, 1.0)
    assert close(state.target, (0.0, 0.0, 0.0), 1e-9)
    p = nav.camera_value()["position"]
    assert close(state.eye, (p[0], -p[2], p[1]))
    assert state.fov_y == 0.8
    assert (state.near, state.far) == (nav.camera_value()["near"], nav.camera_value()["far"])


def test_the_state_is_in_the_stages_frame():
    z_up, y_up = Nav(BOUNDS, up_axis="Z"), Nav(BOUNDS)
    for nav in (z_up, y_up):
        nav.apply_camera_value({"position": [0.0, 5.0, 10.0], "target": [1.0, 2.0, 3.0]})
    assert close(z_up.state().eye, (0.0, -10.0, 5.0))
    assert close(z_up.state().target, (1.0, -3.0, 2.0))
    assert close(y_up.state().eye, (0.0, 5.0, 10.0))
    assert y_up.state().up == (0.0, 1.0, 0.0)


def test_frame_all_centres_a_z_up_stages_bounds():
    nav = Nav(((0.0, 0.0, 0.0), (2.0, 4.0, 6.0)), up_axis="Z")
    nav.frame_all()
    assert close(nav.state().target, (1.0, 2.0, 3.0))
    radius = math.sqrt(4 + 16 + 36) / 2
    assert math.dist(nav.state().eye, nav.state().target) == pytest.approx(
        radius / math.sin(0.4) * 1.05
    )


def test_fly_up_follows_the_stages_up_axis():
    nav = Nav(BOUNDS, up_axis="Z")
    before = nav.state().eye
    fly(nav, "e", 1.0)
    after = nav.state().eye
    assert close(after, (before[0], before[1], before[2] + HALF_DIAGONAL * 0.5), 1e-6)


def test_world_origin_goes_back_to_the_first_look():
    nav = Nav(BOUNDS, up_axis="Z")
    first = nav.camera_value()
    drag(nav, "r", 100, 40)
    drag(nav, "m", 30, 10)
    nav.world_origin()
    assert same_camera(nav.camera_value(), first)


def test_a_new_stage_opens_at_its_own_first_look():
    nav = Nav(BOUNDS)
    drag(nav, "l", 100, 40)
    other = ((-50.0, -50.0, 0.0), (50.0, 50.0, 10.0))
    nav.load(other, up_axis="Z")
    assert same_camera(nav.camera_value(), initial_camera([{"bounds": other}]))
    assert nav.state().up == (0.0, 0.0, 1.0)


# a 3d view's camera as its page emits it (test_scene3d_host's CAMERA)
CAMERA_3D = {
    "position": [3.0, 4.0, 12.5],
    "target": [3.0, 4.0, 2.5],
    "up": [0.0, 1.0, 0.0],
    "fov_y": 0.7,
    "near": 0.1,
    "far": 1000.0,
}


@pytest.mark.parametrize("up_axis", ["Y", "Z"])
def test_a_3d_views_camera_round_trips_through_the_port(up_axis):
    nav = Nav(BOUNDS, up_axis=up_axis)
    nav.apply_camera_value(CameraValue.model_validate(CAMERA_3D))
    out = nav.camera_value()
    assert same_camera(out, CAMERA_3D, 1e-9)
    assert same_camera(CameraValue.model_validate(out).model_dump(mode="json"), out)


def test_two_usd_views_share_a_camera():
    a, b = Nav(BOUNDS, up_axis="Z"), Nav(((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)), up_axis="Z")
    drag(a, "r", 80, -30)
    drag(a, "m", 20, 10)
    b.apply_camera_value(a.camera_value())
    assert close(b.state().eye, a.state().eye)
    assert close(b.state().target, a.state().target)


def test_a_camera_value_without_a_position_is_ignored():
    nav = Nav(BOUNDS)
    before = nav.camera_value()
    nav.apply_camera_value({"target": [1.0, 2.0, 3.0]})
    assert nav.camera_value() == before


# -- adapters: what each renderer is given ------------------------------------------------------
USD_DEFAULT_LENS = (50.0, 20.955, 15.2908)  # UsdGeomCamera: focalLength, apertures (mm)
USD_FOV_Y = 2 * math.atan(15.2908 / 2 / 50.0)


def flat(nested) -> list[float]:
    return [c for row in nested for c in row]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def transform_point(p, m):
    """Row vector times matrix (USD's convention), then the divide by w."""
    v = [p[0], p[1], p[2], 1.0]
    out = [sum(v[k] * m[k][j] for k in range(4)) for j in range(4)]
    return [c / out[3] for c in out[:3]]


def quat_rotate(q, v):
    """v rotated by the unit quaternion q = (w, x, y, z): q v q*."""
    w, x, y, z = q
    tx, ty, tz = 2 * (y * v[2] - z * v[1]), 2 * (z * v[0] - x * v[2]), 2 * (x * v[1] - y * v[0])
    return (
        v[0] + w * tx + (y * tz - z * ty),
        v[1] + w * ty + (z * tx - x * tz),
        v[2] + w * tz + (x * ty - y * tx),
    )


def test_the_usd_camera_of_a_camera_looking_down_minus_z_is_the_identity_turn():
    state = CameraState((0.0, 0.0, 10.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), USD_FOV_Y, 0.1, 500.0)
    cam = usd_camera(state, aspect=20.955 / 15.2908)
    assert flat(cam["transform"]) == pytest.approx(
        flat([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 10, 1]])
    )
    assert cam["focal_length"] == pytest.approx(50.0)
    assert cam["horizontal_aperture"] == pytest.approx(20.955)
    assert cam["vertical_aperture"] == pytest.approx(15.2908)
    assert cam["clipping_range"] == (0.1, 500.0)


def test_the_usd_camera_in_a_z_up_stage_looks_along_y():
    nav = Nav(BOUNDS, up_axis="Z")
    nav.apply_camera_value({"position": [0.0, 0.0, 10.0], "target": [0.0, 0.0, 0.0]})
    # nav (0, 0, 10) is the stage's (0, -10, 0): the camera looks along +Y with Z up
    cam = usd_camera(nav.state(), aspect=1.5)
    assert flat(cam["transform"]) == pytest.approx(
        flat([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, -10, 0, 1]]), abs=1e-12
    )


def test_the_storm_frustum_projects_like_gf_frustum():
    nav = Nav(BOUNDS, up_axis="Z")
    drag(nav, "l", 70, -25, alt=True)
    drag(nav, "m", 10, 30)
    state, aspect = nav.state(), 16 / 9
    f = gf_frustum(state, aspect)
    h = math.tan(state.fov_y / 2)
    assert f["position"] == pytest.approx(state.eye)
    assert flat(f["window"]) == pytest.approx([-aspect * h, -h, aspect * h, h])
    assert f["near_far"] == (state.near, state.far)
    assert f["projection"] == "perspective"
    assert f["view_distance"] == pytest.approx(math.dist(state.eye, state.target))
    # the rotation turns the frustum's -Z / +Y onto the camera's forward and up
    forward = [t - e for t, e in zip(state.target, state.eye, strict=True)]
    forward = [c / math.hypot(*forward) for c in forward]
    assert quat_rotate(f["rotation"], (0.0, 0.0, -1.0)) == pytest.approx(forward)
    assert quat_rotate(f["rotation"], (0.0, 1.0, 0.0))[2] > 0  # Z up stays up
    # view x projection: the target lands mid-screen; the fov's top edge at y = 1
    vp = mat_mul(f["view_matrix"], f["projection_matrix"])
    assert transform_point(state.target, vp)[:2] == pytest.approx([0.0, 0.0], abs=1e-9)
    d = f["view_distance"]
    up = quat_rotate(f["rotation"], (0.0, 1.0, 0.0))
    top = [state.eye[i] + forward[i] * d + up[i] * d * h for i in range(3)]
    assert transform_point(top, vp)[1] == pytest.approx(1.0)
    near = [state.eye[i] + forward[i] * state.near for i in range(3)]
    far = [state.eye[i] + forward[i] * state.far for i in range(3)]
    assert transform_point(near, vp)[2] == pytest.approx(-1.0)
    assert transform_point(far, vp)[2] == pytest.approx(1.0)


def test_the_view_matrix_undoes_the_usd_camera_transform():
    nav = Nav(BOUNDS)
    drag(nav, "r", -50, 20)
    state = nav.state()
    product = mat_mul(usd_camera(state, 1.0)["transform"], gf_frustum(state, 1.0)["view_matrix"])
    identity = [[float(i == j) for j in range(4)] for i in range(4)]
    assert flat(product) == pytest.approx(flat(identity), abs=1e-9)


# -- look through a stage camera ----------------------------------------------------------------
def stage_camera(eye, target, up=(0.0, 0.0, 1.0), fov_y=0.6, near=0.5, far=800.0) -> dict:
    """A UsdGeom.Camera as the source samples it: world transform and lens."""
    cam = usd_camera(CameraState(eye, target, up, fov_y, near, far), aspect=1.0)
    return {
        "transform": cam["transform"],
        "focal_length": cam["focal_length"],
        "vertical_aperture": cam["vertical_aperture"],
        "clipping_range": cam["clipping_range"],
    }


def test_looking_through_a_stage_camera_takes_its_pose_and_lens():
    nav = Nav(BOUNDS, up_axis="Z")
    nav.look_through("/World/Cam", **stage_camera((10.0, -20.0, 5.0), (0.0, 0.0, 2.0)))
    assert nav.looking_through == "/World/Cam"
    state = nav.state()
    assert close(state.eye, (10.0, -20.0, 5.0))
    forward = [t - e for t, e in zip(state.target, state.eye, strict=True)]
    want = (-10.0, 20.0, -3.0)
    assert close([c / math.hypot(*forward) for c in forward], [c / math.hypot(*want) for c in want])
    assert state.fov_y == pytest.approx(0.6)
    assert (state.near, state.far) == pytest.approx((0.5, 800.0))


def test_a_rolled_stage_camera_keeps_its_roll():
    nav = Nav(BOUNDS, up_axis="Z")
    tilted = (math.sin(0.3), 0.0, math.cos(0.3))  # rolled 0.3 rad about the view axis
    nav.look_through("/World/Cam", **stage_camera((0.0, -10.0, 0.0), (0.0, 0.0, 0.0), tilted))
    assert close(nav.state().up, tilted)


def test_an_animated_stage_camera_is_followed_every_frame():
    nav = Nav(BOUNDS, up_axis="Z")
    nav.look_through("/World/Cam", **stage_camera((0.0, -10.0, 1.0), (0.0, 0.0, 1.0)))
    nav.look_through("/World/Cam", **stage_camera((1.0, -10.0, 1.0), (1.0, 0.0, 1.0)))
    assert close(nav.state().eye, (1.0, -10.0, 1.0))


@pytest.mark.parametrize(
    "gesture",
    [
        lambda nav: drag(nav, "l", 10, 0),
        lambda nav: drag(nav, "r", 10, 0),
        lambda nav: drag(nav, "m", 10, 0),
        lambda nav: drag(nav, "l", 10, 0, alt=True),
        lambda nav: nav.wheel(1),
        lambda nav: fly(nav, "w", 0.1),
        lambda nav: nav.frame_all(),
        lambda nav: nav.world_origin(),
        lambda nav: nav.apply_camera_value(CAMERA_3D),
        lambda nav: nav.look_through(None),
    ],
)
def test_navigating_drops_back_to_the_free_camera(gesture):
    nav = Nav(BOUNDS, up_axis="Z")
    nav.look_through("/World/Cam", **stage_camera((10.0, -20.0, 5.0), (0.0, 0.0, 2.0)))
    gesture(nav)
    assert nav.looking_through is None


def test_the_free_camera_carries_on_from_the_stage_cameras_pose():
    nav = Nav(BOUNDS, up_axis="Z")
    nav.look_through("/World/Cam", **stage_camera((10.0, -20.0, 5.0), (0.0, 0.0, 2.0)))
    nav.press("right")
    assert nav.looking_through == "/World/Cam"  # a press alone keeps looking through
    nav.drag(30, 0)
    assert close(nav.state().eye, (10.0, -20.0, 5.0))  # looking around from its eye
    assert nav.state().up == (0.0, 0.0, 1.0)
    nav.release("right")
    nav.look_through("/World/Cam", **stage_camera((10.0, -20.0, 5.0), (0.0, 0.0, 2.0)))
    target = nav.state().target
    drag(nav, "l", 30, 0, alt=True)
    assert close(nav.state().target, target, 1e-9)  # orbiting the point it looked at


# -- coalesced changes: one set_camera per tick, jumps marked as cuts ---------------------------
def test_a_change_is_taken_once():
    nav = Nav(BOUNDS)
    assert nav.take_change() == (nav.state(), True)  # the first look is a cut
    assert nav.take_change() is None
    drag(nav, "r", 30, 0)
    drag(nav, "r", 30, 0)
    assert nav.take_change() == (nav.state(), False)  # two drags, one change, no cut
    assert nav.take_change() is None


@pytest.mark.parametrize(
    "jump",
    [
        lambda nav: nav.frame_all(),
        lambda nav: nav.world_origin(),
        lambda nav: nav.apply_camera_value(CAMERA_3D),
        lambda nav: nav.look_through("/World/Cam", **stage_camera((5.0, -9.0, 2.0), (0, 0, 0))),
        lambda nav: nav.load(((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)), "Z"),
    ],
)
def test_jumps_are_cuts(jump):
    nav = Nav(BOUNDS, up_axis="Z")
    drag(nav, "r", 40, 10)
    nav.take_change()
    jump(nav)
    change = nav.take_change()
    assert change is not None
    assert change == (nav.state(), True)


def test_following_an_animated_camera_is_not_a_cut():
    nav = Nav(BOUNDS, up_axis="Z")
    nav.look_through("/World/Cam", **stage_camera((0.0, -10.0, 1.0), (0.0, 0.0, 1.0)))
    nav.take_change()
    nav.look_through("/World/Cam", **stage_camera((0.1, -10.0, 1.0), (0.1, 0.0, 1.0)))
    assert nav.take_change() == (nav.state(), False)


def test_a_jump_to_where_the_camera_already_is_changes_nothing():
    nav = Nav(BOUNDS)
    nav.take_change()
    nav.world_origin()
    assert nav.take_change() is None


def test_frame_bounds_fits_one_prim_and_keeps_the_stages_clip_range():
    # F on a selection (#151), an outliner double-click (#140): world bounds, stage frame
    nav = Nav(((-100.0, -100.0, 0.0), (100.0, 100.0, 20.0)), up_axis="Z")
    nav.frame_all()
    far = nav.state().far
    nav.frame_bounds(((10.0, 20.0, 0.0), (12.0, 24.0, 6.0)))
    state = nav.state()
    assert close(state.target, (11.0, 22.0, 3.0))
    radius = math.sqrt(4 + 16 + 36) / 2
    assert math.dist(state.eye, state.target) == pytest.approx(radius / math.sin(0.4) * 1.05)
    assert state.far == far  # the rest of the stage stays in view
    assert state.near == pytest.approx(max(0.001, radius / 5000))


def test_f_after_flying_away_brings_the_stage_back_ahead():
    # #151: framing turns nothing, it puts the box's centre ahead at the framing distance
    nav = Nav(((-70.0, -60.0, 0.0), (70.0, 80.0, 12.0)), up_axis="Z")  # a depot-sized box
    drag(nav, "r", 200, -50)
    fly(nav, "w", 3.0)
    yaw, pitch = nav.yaw, nav.pitch
    nav.frame_all()
    assert (nav.yaw, nav.pitch) == pytest.approx((yaw, pitch))
    assert close(nav.state().target, (0.0, 10.0, 6.0), 1e-9)


def test_f_on_a_picked_point_frames_a_small_cube_around_it():
    nav = Nav(BOUNDS, up_axis="Z")
    yaw, pitch = nav.yaw, nav.pitch
    nav.frame_point((1.0, 2.0, 3.0))
    state = nav.state()
    assert close(state.target, (1.0, 2.0, 3.0), 1e-9)
    radius = math.sqrt(3) * HALF_DIAGONAL * nav_mod.POINT_FRAME / 2  # the cube's half-diagonal
    assert math.dist(state.eye, state.target) == pytest.approx(radius / math.sin(0.4) * 1.05)
    assert (nav.yaw, nav.pitch) == pytest.approx((yaw, pitch))  # the view keeps its direction
