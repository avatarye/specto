"""Seam 2 (#151): the usd view host's side of Unreal Editor's viewport scheme, headless
against the stub USD source. F frames the selection's bounds, else the whole stage; an
orbit's (Alt+LMB) pivot is the selection's centre, else the point picked under the press
(which selects nothing); the wheel with RMB held sets the camera speed live and keeps it
with one user submit when RMB comes up."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_usd_host import health, stages, submit_usd

from specto_app.hosts import HostContext
from specto_app.usd.camera import NavCamera
from specto_app.views.usd_view import USD_MANIFEST, PlaceholderCamera, UsdHost
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
