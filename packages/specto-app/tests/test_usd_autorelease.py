"""On macOS the usd frame source drains an Objective-C autorelease pool around every turn
of its main-thread loop, and around starting and closing the renderer. The source never
runs a Cocoa event loop, so without one nothing drains the Metal objects Storm's
engines autorelease (HgiMetal), and every stage load kept them: about 6 MB of physical
footprint per load on a 200-cube stage, 60 MB on Kitchen_set, none of it in RSS."""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
import time
from pathlib import Path

import pytest

from specto_app.usd import autorelease, source

DARWIN = sys.platform == "darwin"


def test_off_macos_the_pool_is_a_no_op(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(autorelease, "_objc", lambda: calls.append("loaded"))
    with autorelease.autorelease_pool():
        pass
    assert calls == []


@pytest.mark.skipif(not DARWIN, reason="the Objective-C runtime is macOS's")
def test_on_macos_each_pool_is_pushed_and_popped_in_order():
    with autorelease.autorelease_pool():
        with autorelease.autorelease_pool():
            pass
    # a pop with the wrong token would have aborted the process; a raise still pops
    with pytest.raises(ValueError), autorelease.autorelease_pool():
        raise ValueError("inside")


def test_the_source_drains_a_pool_around_every_turn_and_the_renderer(monkeypatch):
    entered: list[str] = []

    @contextlib.contextmanager
    def pool():
        entered.append("pool")
        yield

    monkeypatch.setattr(source, "autorelease_pool", pool)
    src = object.__new__(source.UsdSource)
    turns = iter([True, True, False])
    src.control = type(
        "C",
        (),
        {"start": lambda s: None, "emit": lambda s, *a, **k: None, "wait_eof": lambda s: None},
    )()
    src.frames = type("F", (), {"ring_name": "r", "wake": "w", "close": lambda s: None})()
    src.width = src.height = 1
    log: list[str] = []
    src._start_renderer = lambda: log.append(f"start in pool {len(entered)}")
    src._next = lambda: (log.append(f"turn in pool {len(entered)}"), next(turns))[1]
    src._end_overlay = lambda: None
    src._close_renderer = lambda: log.append(f"close in pool {len(entered)}")
    assert src.run() == 0
    # each step ran inside a pool of its own: the count went up before each one
    assert log == [
        "start in pool 1",
        "turn in pool 2",
        "turn in pool 3",
        "turn in pool 4",
        "close in pool 5",
    ]


# -- the leak itself, on a real Storm source (macOS with an OpenUSD imaging build) ---------
class _Rusage(ctypes.Structure):  # rusage_info_v2's head, up to ri_phys_footprint
    _fields_ = [("uuid", ctypes.c_uint8 * 16)]
    _fields_ += [(f"f{i}", ctypes.c_uint64) for i in range(7)]
    _fields_ += [("phys_footprint", ctypes.c_uint64), ("rest", ctypes.c_uint64 * 24)]


def footprint_mb(pid: int) -> float:
    """A process's physical footprint, which counts the GPU memory it owns (RSS does not)."""
    ru = _Rusage()
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    assert libc.proc_pid_rusage(pid, 2, ctypes.byref(ru)) == 0
    return ru.phys_footprint / 2**20


def storm_present() -> bool:
    from specto_core.machine import detect_storm

    return DARWIN and detect_storm().present


def cubes(path: Path, n: int) -> Path:
    lines = ["#usda 1.0", "(", '    upAxis = "Z"', ")", 'def Xform "World" {']
    for i in range(n):
        lines += [
            f'    def Cube "C{i}" {{',
            f"        double3 xformOp:translate = ({i % 20 * 3}, {i // 20 * 3}, 0)",
            '        uniform token[] xformOpOrder = ["xformOp:translate"]',
            "    }",
        ]
    path.write_text("\n".join([*lines, "}"]) + "\n", encoding="utf-8")
    return path


def footprint_growth(tmp_path: Path, big_cubes: int, loads: int) -> float:
    """MB a Storm source's physical footprint grows over ``loads`` pairs of loads (a
    ``big_cubes`` stage, then a 1-cube one), after one pair of warm-up."""
    from specto_app.frames import control
    from specto_app.views.usd_view import storm_launcher

    big = cubes(tmp_path / "big.usda", big_cubes)
    small = cubes(tmp_path / "small.usda", 1)
    src = control.FrameSourceProcess.spawn(storm_launcher(640, 360), env=dict(os.environ))

    def load(path: Path) -> None:
        src.send("load", path=str(path))
        src.wait_event("loaded", timeout=120)
        while src.wait_event("health", timeout=120).get("state") not in ("ok", "error"):
            pass

    try:
        src.wait_event("ready", timeout=60)
        load(big), load(small)
        time.sleep(1)
        before = footprint_mb(src.proc.pid)
        for _ in range(loads):
            load(big), load(small)
        time.sleep(1)
        return footprint_mb(src.proc.pid) - before
    finally:
        src.close()


@pytest.mark.skipif(not storm_present(), reason="macOS with an OpenUSD imaging build")
def test_stage_loads_do_not_keep_storms_metal_memory(tmp_path):
    grown = footprint_growth(tmp_path, 200, 10)
    # without the pools: about 115 MB here; with them about 10
    assert grown < 40, f"the footprint grew {grown:.0f} MB over 10 loads"


@pytest.mark.skipif(not storm_present(), reason="macOS with an OpenUSD imaging build")
def test_an_engine_releases_its_metal_objects_with_the_patched_openusd(tmp_path):
    """tools/parked/openusd-patches/0001: HgiMetal's indirect command encoder (the ICB
    and argument buffer pools), its compute pipelines and the Metal-GL interop released
    nothing on destruction. Each engine makes its own Hgi, so each stage load left them:
    about 6.6 MB a load here with an unpatched build, about 1 MB with the patch."""
    grown = footprint_growth(tmp_path, 2000, 8)
    assert grown < 25, (
        f"the footprint grew {grown:.0f} MB over 8 loads: is the OpenUSD build patched? "
        "rebuild it with tools/parked/build_openusd.py"
    )
