"""Seam 2 (#151): the usd view host's side of Unreal Editor's viewport scheme, headless
against the stub USD source. F frames the selection's bounds, each prim's whole subtree
(#39), else the whole stage; an orbit's (Alt+LMB) pivot is the selection's centre, else
the point picked under the press (which selects nothing); the wheel with RMB held sets
the camera speed live and keeps it with one user submit when RMB comes up. Orbit mode
(#39): ``params.camera_mode``, one submit, the selection's centre as the standing pivot
and the status bar's line following the mode."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_usd_host import health, stages, submit_usd

from specto_app.hosts import HostContext
from specto_app.usd.camera import NavCamera
from specto_app.views.usd_view import ORBIT_KEYS, USD_MANIFEST, PlaceholderCamera, UsdHost
from specto_core import Engine
from specto_core.jobs import InlineJobManager

BLOCK_1 = "/World/Blocks/Block_01"
BLOCK_3 = "/World/Blocks/Block_03"
LEFT, RIGHT, MIDDLE = 1, 2, 4  # Qt's buttons, as QML hands them to the bridge


def open_host(tmp_path: Path, spin, camera) -> UsdHost:
    engine = Engine(project=None, jobs=InlineJobManager())
    submit_usd(engine)
    host = UsdHost(
        HostContext(engine, None), "kobi", USD_MANIFEST, headless=True, camera_factory=camera
    )
    host.receive("data", stages(tmp_path / "w", "valley"))
    spin(until=lambda: health(host)["status"] == "ok")
    return host


@pytest.fixture
def placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp, spin):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    host = open_host(tmp_path, spin, PlaceholderCamera)
    yield host
    host.dispose()


@pytest.fixture
def nav(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp, spin):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    host = open_host(tmp_path, spin, NavCamera)
    yield host
    host.dispose()


def calls(host: UsdHost, name: str) -> list[tuple]:
    return [c for c in host.camera.calls if c[0] == name]


def source_camera(host: UsdHost) -> dict:
    return host.snapshot()["source"].get("camera") or {}


def test_f_frames_the_bounds_around_every_selected_prim(placeholder: UsdHost, spin):
    placeholder.select([BLOCK_1, BLOCK_3])
    placeholder.bridge.frame()
    # the stub's blocks are 16 x 12 x 12 at x = -40 and 40
    spin(until=lambda: calls(placeholder, "frame"), timeout=10)
    assert calls(placeholder, "frame") == [("frame", [[-48.0, -6.0, 0.0], [48.0, 6.0, 12.0]])]
    assert calls(placeholder, "frame_all") == []


def test_f_with_nothing_selected_frames_the_whole_stage(placeholder: UsdHost):
    placeholder.bridge.frame()
    assert calls(placeholder, "frame_all") == [("frame_all",)]


def test_f_moves_the_real_camera_onto_the_selection_then_the_stage(nav: UsdHost, spin):
    nav.select([BLOCK_1])
    nav.bridge.frame()
    spin(until=lambda: source_camera(nav).get("target") == pytest.approx([-40.0, 0.0, 6.0]))
    assert source_camera(nav)["cut"] is True
    nav.select([])
    nav.bridge.frame()
    spin(until=lambda: source_camera(nav).get("target") == pytest.approx([0.0, 0.0, 6.0]))


def test_an_alt_press_pivots_on_the_point_under_it_without_selecting(placeholder: UsdHost, spin):
    # NDC (-0.7, -0.6): the stub's first block, its base (hit at (-40, -6, 4))
    placeholder.bridge.press(LEFT, True, -0.7, -0.6)
    spin(until=lambda: calls(placeholder, "pivot"), timeout=10)
    assert calls(placeholder, "pivot") == [("pivot", [-40.0, -6.0, 4.0])]
    assert placeholder.selected == []
    placeholder.bridge.release(LEFT)


def test_an_alt_press_on_a_selection_pivots_on_its_centre(placeholder: UsdHost, spin):
    placeholder.select([BLOCK_3])
    placeholder.bridge.press(LEFT, True, -0.7, -0.6)
    spin(until=lambda: calls(placeholder, "pivot"), timeout=10)
    assert calls(placeholder, "pivot") == [("pivot", [40.0, 0.0, 6.0])]


def test_only_an_orbit_asks_for_a_pivot(placeholder: UsdHost, spin):
    placeholder.bridge.press(LEFT, False, -0.7, -0.6)
    placeholder.bridge.release(LEFT)
    placeholder.bridge.press(RIGHT, True, -0.7, -0.6)  # Alt+RMB dollies along the view
    placeholder.bridge.release(RIGHT)
    spin(200)
    assert calls(placeholder, "pivot") == []


def test_the_wheel_with_rmb_held_sets_the_speed_and_keeps_it_on_release(nav: UsdHost):
    b = nav.bridge
    b.press(RIGHT, False, 0.0, 0.0)
    b.release(RIGHT)
    assert "camera_speed" not in nav.params  # RMB alone: no submit
    b.press(RIGHT, False, 0.0, 0.0)
    b.wheel(480)  # four notches of 0.25 in log2: x2
    assert nav.camera_speed == 2.0 and b.speed == 2.0
    assert "camera_speed" not in nav.params  # live while RMB is held
    b.release(RIGHT)
    assert nav.params["camera_speed"] == 2.0  # one user submit


def test_the_wheel_alone_dollies(nav: UsdHost, spin):
    spin(until=lambda: source_camera(nav))
    before = source_camera(nav)["position"]
    nav.bridge.wheel(120)
    spin(until=lambda: source_camera(nav)["position"] != before)
    assert nav.camera_speed == 1.0
    assert source_camera(nav)["cut"] is False


def test_chords_and_the_middle_button_pan(nav: UsdHost, spin):
    spin(until=lambda: source_camera(nav))
    before = source_camera(nav)
    b = nav.bridge
    b.press(LEFT, False, 0.0, 0.0)
    b.press(RIGHT, False, 0.0, 0.0)
    b.drag(0, -40)  # up: the camera rises up the screen (the stage is Z-up)
    b.release(RIGHT)
    b.release(LEFT)
    spin(until=lambda: source_camera(nav)["position"] != before["position"])
    after = source_camera(nav)
    assert after["position"][2] > before["position"][2]
    moved = [a - b for a, b in zip(after["position"], before["position"], strict=True)]
    shifted = [a - b for a, b in zip(after["target"], before["target"], strict=True)]
    assert moved == pytest.approx(shifted)  # eye and target together
    b.press(MIDDLE, False, 0.0, 0.0)
    b.drag(0, 40)
    b.release(MIDDLE)
    spin(until=lambda: source_camera(nav)["position"] != after["position"])
    assert source_camera(nav)["position"] == pytest.approx(before["position"])


def test_an_alt_press_on_a_picked_selection_pivots_on_the_picked_point(placeholder: UsdHost, spin):
    placeholder.pick(-0.7, -0.6)  # the first block's base, hit at (-40, -6, 4)
    spin(until=lambda: placeholder.selected == [f"{BLOCK_1}/Base"], timeout=10)
    placeholder.bridge.press(LEFT, True, 0.7, 0.6)
    assert calls(placeholder, "pivot") == [("pivot", [-40.0, -6.0, 4.0])]


def test_f_on_a_picked_prim_without_bounds_frames_the_picked_point(
    placeholder: UsdHost, spin, monkeypatch: pytest.MonkeyPatch
):
    placeholder.pick(-0.7, -0.6)
    spin(until=lambda: placeholder.selected == [f"{BLOCK_1}/Base"], timeout=10)
    monkeypatch.setattr(placeholder, "_selection_bounds", lambda take: take(None))
    placeholder.bridge.frame()
    assert calls(placeholder, "frame_point") == [("frame_point", [-40.0, -6.0, 4.0])]


# -- F frames the whole subtree (#39) ------------------------------------------------------------
def test_f_on_a_parent_prim_frames_everything_under_it(placeholder: UsdHost, spin):
    # the stub's Blocks scope draws nothing itself; its three blocks span x -48 .. 48
    placeholder.select(["/World/Blocks"])
    placeholder.bridge.frame()
    spin(until=lambda: calls(placeholder, "frame"), timeout=10)
    assert calls(placeholder, "frame") == [("frame", [[-48.0, -6.0, 0.0], [48.0, 6.0, 12.0]])]
    placeholder.select([f"{BLOCK_1}/Top"])  # a child: its own box, the block's upper half
    placeholder.bridge.frame()
    spin(until=lambda: len(calls(placeholder, "frame")) == 2, timeout=10)
    assert calls(placeholder, "frame")[-1] == ("frame", [[-48.0, -6.0, 6.0], [-32.0, 6.0, 12.0]])


def test_f_on_a_prim_that_draws_nothing_frames_the_whole_stage(placeholder: UsdHost, spin):
    placeholder.select(["/World/Vehicles"])  # the stub's vans draw nothing
    placeholder.bridge.frame()
    spin(until=lambda: calls(placeholder, "frame_all"), timeout=10)
    assert calls(placeholder, "frame") == [] and calls(placeholder, "frame_point") == []


# -- orbit mode (#39) --------------------------------------------------------------------------
def test_the_camera_mode_is_a_param_one_submit_shown_and_snapped(nav: UsdHost, spin):
    undo_depth = nav.ctx.live.undo_info()["depth"]
    assert nav.camera_mode == "free" and nav.bridge.cameraMode == "free"
    assert nav.snapshot()["camera_mode"] == "free" and nav.nav_keys == ""
    nav.bridge.setCameraMode("orbit")
    assert nav.params["camera_mode"] == "orbit"
    assert nav.ctx.live.undo_info()["depth"] == undo_depth + 1
    assert nav.camera_mode == "orbit" and nav.bridge.cameraMode == "orbit"
    assert nav.camera.mode == "orbit" and nav.snapshot()["camera_mode"] == "orbit"
    assert nav.nav_keys == ORBIT_KEYS
    nav.bridge.setCameraMode("orbit")  # the same again: nothing
    assert nav.ctx.live.undo_info()["depth"] == undo_depth + 1
    nav.bridge.setCameraMode("sideways")  # not a mode: free, one submit
    assert nav.params["camera_mode"] == "free" and nav.camera.mode == "free"
    assert nav.ctx.live.undo_info()["depth"] == undo_depth + 2


def test_the_agents_camera_mode_param_switches_the_mode(nav: UsdHost, spin):
    doc = nav.ctx.live.document()
    doc["views"]["kobi"]["params"]["camera_mode"] = "orbit"
    nav.ctx.live.submit(doc, origin="agent:s1", label="orbit")
    nav.on_patch([{"op": "add", "path": "/views/kobi/params/camera_mode"}], {})
    assert nav.camera_mode == "orbit"
    assert nav.camera.mode == "orbit" and nav.bridge.cameraMode == "orbit"


def test_switching_the_mode_keeps_the_camera_where_it_is(nav: UsdHost, spin):
    spin(until=lambda: source_camera(nav))
    nav.bridge.press(RIGHT, False, 0.0, 0.0)
    nav.bridge.drag(60, -20)
    nav.bridge.release(RIGHT)
    spin(200)
    before = source_camera(nav)
    nav.bridge.setCameraMode("orbit")
    spin(200)
    assert source_camera(nav) == before
    nav.bridge.setCameraMode("free")
    spin(200)
    assert source_camera(nav) == before


def test_the_status_bar_line_follows_the_mode():
    from specto_app.canvas.controller import GESTURES, NAV_KEYS, status_hint
    from specto_app.hosts.stepping import step_hint

    info = {"id": "kobi", "type": "usd", "health": "ok", "healthMessage": "w.usda"}
    steps = step_hint("usd")
    assert status_hint({**info, "keys": ""}, hovered=True) == (
        f"kobi: w.usda  ·  {steps}  ·  {NAV_KEYS}  ·  {GESTURES}"
    )
    assert status_hint({**info, "keys": ORBIT_KEYS}, hovered=True) == (
        f"kobi: w.usda  ·  {steps}  ·  {ORBIT_KEYS}  ·  {GESTURES}"
    )
    assert "F frames" in ORBIT_KEYS
    assert status_hint({**info, "keys": ORBIT_KEYS}, hovered=False) == "kobi: w.usda"


def test_in_orbit_mode_the_selections_centre_is_the_standing_pivot(placeholder: UsdHost, spin):
    placeholder.bridge.setCameraMode("orbit")
    placeholder.select([BLOCK_1, BLOCK_3])
    spin(until=lambda: calls(placeholder, "anchor"), timeout=10)
    assert calls(placeholder, "anchor")[-1] == ("anchor", [0.0, 0.0, 6.0])  # their centre
    placeholder.bridge.press(LEFT, False, -0.7, -0.6)  # no Alt: an orbit all the same
    placeholder.bridge.release(LEFT)
    placeholder.bridge.press(RIGHT, False, -0.7, -0.6)  # a dolly toward it
    placeholder.bridge.release(RIGHT)
    spin(200)
    assert calls(placeholder, "pivot") == []  # the anchor stands: nothing asked
    placeholder.select([])
    spin(until=lambda: calls(placeholder, "anchor")[-1] == ("anchor", None), timeout=10)


def test_in_orbit_mode_without_a_selection_the_press_point_pivots(placeholder: UsdHost, spin):
    placeholder.bridge.setCameraMode("orbit")
    placeholder.bridge.press(LEFT, False, -0.7, -0.6)  # the first block's base
    spin(until=lambda: calls(placeholder, "pivot"), timeout=10)
    assert calls(placeholder, "pivot") == [("pivot", [-40.0, -6.0, 4.0])]
    assert placeholder.selected == []  # a press selects nothing
    placeholder.bridge.release(LEFT)
    placeholder.bridge.press(MIDDLE, False, -0.7, -0.6)  # a pan: no pivot
    placeholder.bridge.release(MIDDLE)
    spin(200)
    assert len(calls(placeholder, "pivot")) == 1


def test_a_prim_that_draws_nothing_leaves_no_anchor(placeholder: UsdHost, spin):
    placeholder.bridge.setCameraMode("orbit")
    placeholder.select([BLOCK_3])
    spin(until=lambda: calls(placeholder, "anchor")[-1][1] is not None, timeout=10)
    placeholder.select(["/World/Vehicles"])  # the stub's vans draw nothing
    spin(until=lambda: calls(placeholder, "anchor")[-1] == ("anchor", None), timeout=10)


def test_switching_to_orbit_takes_the_anchor_of_a_selection_already_made(
    placeholder: UsdHost, spin
):
    before = len(calls(placeholder, "anchor"))  # the stage opening dropped it
    placeholder.select([BLOCK_3])
    spin(200)
    assert calls(placeholder, "anchor")[before:] == []  # the free camera needs none
    placeholder.bridge.setCameraMode("orbit")
    spin(until=lambda: calls(placeholder, "anchor")[before:], timeout=10)
    assert calls(placeholder, "anchor")[before:] == [("anchor", [40.0, 0.0, 6.0])]


def test_in_orbit_mode_the_real_camera_turns_around_the_selection(nav: UsdHost, spin):
    nav.select([BLOCK_3])
    nav.bridge.frame()
    spin(until=lambda: source_camera(nav).get("target") == pytest.approx([40.0, 0.0, 6.0]))
    nav.bridge.setCameraMode("orbit")
    spin(200)
    before = source_camera(nav)
    nav.bridge.press(LEFT, False, 0.0, 0.0)
    nav.bridge.drag(120, 0)
    nav.bridge.release(LEFT)
    spin(until=lambda: source_camera(nav)["position"] != before["position"])
    after = source_camera(nav)
    assert after["target"] == pytest.approx([40.0, 0.0, 6.0])  # still looking at the block

    def reach(cam: dict) -> float:
        return sum((a - b) ** 2 for a, b in zip(cam["position"], (40, 0, 6), strict=True))

    assert reach(after) == pytest.approx(reach(before))  # the same distance from it
    assert after["position"][2] == pytest.approx(before["position"][2])  # a turn about Z
