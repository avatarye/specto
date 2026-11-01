"""Seam 2 (#138, #151): the usd view's 60 Hz camera tick runs only while the camera
flies: RMB and a fly key held, a held state ticked every frame (key repeat plays no
part). A drag or the wheel moves the camera at once and is handed over by ``kick``
without ticking; a jump (frame all, World origin, a camera port value) goes out at once,
as a cut."""

from __future__ import annotations

from specto_app.usd.nav import Nav
from specto_app.usd.ticker import NavTicker

BOUNDS = ((-4.0, -1.0, -3.0), (6.0, 2.0, 5.0))


def make(qapp):
    nav = Nav(BOUNDS, up_axis="Z")
    nav.take_change()  # the first look went out with the load
    changes = []
    ticker = NavTicker(nav, lambda state, cut: changes.append((state, cut)))
    return nav, ticker, changes


def test_a_drag_goes_out_at_once_without_ticking(qapp, spin):
    nav, ticker, changes = make(qapp)
    assert ticker.interval_ms == 16
    nav.press("left")
    nav.drag(60, 0)
    ticker.kick()
    assert changes == [(nav.state(), False)]
    assert not ticker.running


def test_an_idle_camera_does_not_tick(qapp, spin):
    _nav, ticker, changes = make(qapp)
    ticker.kick()
    assert not ticker.running
    assert changes == []


def test_a_held_fly_key_keeps_ticking_until_it_is_let_go(qapp, spin):
    nav, ticker, changes = make(qapp)
    nav.set_key("w", True)
    ticker.kick()
    assert not ticker.running  # no right button: no flying
    nav.press("right")
    ticker.kick()
    spin(150)
    assert ticker.running
    moved = len(changes)
    assert moved >= 3
    nav.set_key("w", False)
    spin(until=lambda: not ticker.running, timeout=1.0)
    assert not ticker.running
    assert len(changes) <= moved + 1


def test_a_jump_goes_out_at_once_as_a_cut(qapp, spin):
    nav, ticker, changes = make(qapp)
    nav.frame_all()
    ticker.kick()
    assert changes == [(nav.state(), True)]
    assert not ticker.running


def test_stop_halts_the_tick(qapp, spin):
    nav, ticker, _changes = make(qapp)
    nav.press("right")
    nav.set_key("d", True)
    ticker.kick()
    ticker.stop()
    assert not ticker.running
    assert not nav.moving  # the held key is dropped too (a paused or hidden view)
