"""``specto-usd-source``, Storm flavour (#143), driven over the frame-source protocol: Hydra
Storm from the OpenUSD imaging build draws a small authored stage offscreen (no window)
into the frame ring, the camera moves it, and a pick names the prim under a point.
Skipped where this machine has no Storm (the build or the storm extra is missing)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from specto_app.frames import control
from specto_app.frames.ring import RingReader
from specto_core.machine import detect_storm, openusd_prefix

STORM = detect_storm()
pytestmark = pytest.mark.skipif(not STORM.present, reason=STORM.reason)

# a red box at the origin, 2 m wide, on a Z-up stage; a blue ball off to +X
STAGE = """#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)

def Xform "World"
{
    def Cube "Box"
    {
        double size = 2
        color3f[] primvars:displayColor = [(0.9, 0.1, 0.1)]
    }

    def Sphere "Ball"
    {
        double radius = 1
        color3f[] primvars:displayColor = [(0.1, 0.1, 0.9)]
        double3 xformOp:translate = (6, 0, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
}
"""
W, H = 160, 90
IN_FLIGHT = 2  # frames the source may have drawn before it took the last request


@pytest.fixture
def stage_file(tmp_path: Path) -> Path:
    path = tmp_path / "boxes.usda"
    path.write_text(STAGE, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def source(tmp_path: Path):
    argv = [sys.executable, "-m", "specto_app.usd.source", "--renderer", "storm"]
    argv += ["--openusd", openusd_prefix(), "--width", str(W), "--height", str(H)]
    # a process that imported usd-core's pxr hands down its DLL folder in this variable,
    # which pxr.Tf prefers to PATH: the source must still load the build's
    env = {**os.environ, "PXR_USD_WINDOWS_DLL_PATH": str(tmp_path)}
    src = control.FrameSourceProcess.spawn(argv, env=env)
    yield src
    src.close()


def wait_health(src: control.FrameSourceProcess, state: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        params = src.wait_event("health", timeout=max(0.1, deadline - time.monotonic()))
        if params.get("state") == "error":
            raise AssertionError(f"the source failed: {params}")
        if params.get("state") == state:
            return params


def wait_frame(reader: RingReader, timeout: float = 10.0, after: int = 0, **want):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        f = reader.latest()
        if f is not None and f.seq > after and all(getattr(f, k) == v for k, v in want.items()):
            return f
        time.sleep(0.005)
    raise AssertionError(f"no frame with {want}")


def pixel(frame, x: int, y: int) -> tuple[int, ...]:
    at = y * frame.stride + x * 4
    return tuple(frame.pixels[at : at + 4])


def look(src, position, target, cut=True) -> None:
    src.send(
        "set_camera",
        position=list(position),
        target=list(target),
        up=[0, 0, 1],
        fov_y=0.8,
        near=0.1,
        far=1000.0,
        cut=cut,
    )


def load(src, path: Path) -> RingReader:
    ready = src.wait_event("ready", timeout=30)
    src.send("load", path=str(path))
    loaded = src.wait_event("loaded", timeout=30)
    assert loaded["renderer"] == "storm" and loaded["up_axis"] == "Z"
    wait_health(src, "ok")
    return RingReader.open(ready["ring"])


def test_storm_draws_the_stage_into_the_ring(source, stage_file):
    reader = load(source, stage_file)
    try:
        look(source, (0, -8, 0), (0, 0, 0))  # the box fills the middle, the ball off right
        f = wait_frame(reader, width=W, height=H, after=reader.latest().seq + IN_FLIGHT)
        r, g, b, a = pixel(f, W // 2, H // 2)
        assert a == 255 and r > 2 * g and r > 2 * b and r > 60, (r, g, b)  # the lit red box
        corner = pixel(f, 2, 2)
        assert corner != (0, 0, 0, 0) and abs(corner[0] - corner[2]) < 10  # a grey clear
    finally:
        reader.close()


def test_the_camera_and_size_follow_the_host_and_an_idle_view_stops_drawing(source, stage_file):
    reader = load(source, stage_file)
    try:
        look(source, (6, -8, 0), (6, 0, 0))  # turned to the ball
        f = wait_frame(reader, after=reader.latest().seq + IN_FLIGHT)  # past the old look
        r, g, b, _ = pixel(f, W // 2, H // 2)
        assert b > 2 * r and b > 2 * g, (r, g, b)

        assert source.call("resize", width=120, height=100) == {"width": 120, "height": 100}
        f = wait_frame(reader, width=120, height=100)  # within the ring: no successor
        r, g, b, _ = pixel(f, 60, 50)
        assert b > 2 * r and b > 2 * g, (r, g, b)  # the same look in the new size

        deadline = time.monotonic() + 5
        while source.call("state")["stepping"] and time.monotonic() < deadline:
            time.sleep(0.05)
        steps = source.call("state")["steps"]
        time.sleep(0.3)
        assert source.call("state")["steps"] == steps  # idle: no frames drawn
    finally:
        reader.close()


def test_a_pick_names_the_prim_under_a_point_with_where_it_was_hit(source, stage_file):
    load(source, stage_file).close()
    look(source, (3, -12, 0), (3, 0, 0))  # the box left of the middle, the ball right
    # the box's front face (y = -1) is 11 m away, where the view is 2 * 11 * tan(0.4) * 16/9
    # = 16.54 m wide: its middle (x = 0) is at NDC -3 / 8.27 = -0.363
    box = source.call("pick", x=-0.363, y=0.0)
    assert box["hit"] and box["path"] == "/World/Box"
    assert box["position"] == pytest.approx([0, -1, 0], abs=0.02)
    assert box["normal"] == pytest.approx([0, -1, 0], abs=0.01)
    ball = source.call("pick", x=0.33, y=0.0)
    assert ball["hit"] and ball["path"] == "/World/Ball"
    off = [p - c for p, c in zip(ball["position"], (6, 0, 0), strict=True)]
    assert sum(v * v for v in off) ** 0.5 == pytest.approx(1, abs=0.02)  # on its surface
    assert source.call("pick", x=0.0, y=0.9) == {"hit": False}  # the clear above


def test_select_highlights_the_prims_in_the_colour_asked_for(source, stage_file):
    # #140: Storm's own selection highlight, tinted with the host's colour
    reader = load(source, stage_file)
    try:
        look(source, (0, -8, 0), (0, 0, 0))  # the red box fills the middle
        f = wait_frame(reader, 30, after=reader.latest().seq + IN_FLIGHT)  # past the old look
        before = pixel(f, W // 2, H // 2)
        assert source.call("select", paths=["/World"], color="#00ff00") == {}
        assert source.call("state")["selected"] == ["/World"]
        f = wait_frame(reader, 30, after=reader.latest().seq + IN_FLIGHT)
        lit = pixel(f, W // 2, H // 2)
        assert lit[1] > before[1] + 40, (before, lit)  # greener
        source.call("select", paths=[])
        f = wait_frame(reader, 30, after=reader.latest().seq + IN_FLIGHT)
        assert pixel(f, W // 2, H // 2)[1] == pytest.approx(before[1], abs=8)
    finally:
        reader.close()


def test_without_ovrtx_the_view_renders_with_storm_and_says_what_it_cannot_show(
    tmp_path: Path, qapp, spin
):
    """The usd view's host on a machine with Storm and no ovrtx: ``auto`` starts the Storm
    source, and the VIEW panel's renderer readout says MDL materials show grey."""
    from test_usd_host import stages, submit_usd

    from specto_app.hosts import HostContext
    from specto_app.views.usd_view import UsdFactory
    from specto_core import Engine
    from specto_core.jobs import InlineJobManager
    from specto_core.machine import MachineCapability

    caps = {"ovrtx": MachineCapability(name="ovrtx", present=False), "storm": STORM}
    engine = Engine(project=None, jobs=InlineJobManager(), capabilities=lambda: caps)
    submit_usd(engine, {"renderer": "auto"})
    host = UsdFactory().create(HostContext(engine, None), "kobi", engine.plugins.view("usd"))
    try:
        host.receive("data", stages(tmp_path / "w", "valley"))
        spin(until=lambda: host.ctx.health_of("kobi")["status"] == "ok", timeout=30)
        snap = host.snapshot()
        assert snap["renderer"] == "storm" and snap["source"]["steps"] > 0
        assert snap["renderer_note"] == "MDL materials show grey; no sky"
        assert host.bridge.property("renderer") == "Storm"
        assert host.bridge.property("rendererNote") == "MDL materials show grey; no sky"
    finally:
        host.dispose()


# #141: the box crosses from x = -4 to x = 4 in two seconds (48 timecodes at 24 per second)
ANIMATED = STAGE.replace(
    """        double size = 2
        color3f[] primvars:displayColor = [(0.9, 0.1, 0.1)]
    }""",
    """        double size = 2
        color3f[] primvars:displayColor = [(0.9, 0.1, 0.1)]
        double3 xformOp:translate.timeSamples = { 0: (-4, 0, 0), 48: (4, 0, 0) }
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }""",
).replace('    upAxis = "Z"\n', '    upAxis = "Z"\n    startTimeCode = 0\n    endTimeCode = 48\n')


def test_set_time_renders_the_stage_at_that_time(source, tmp_path):
    """#141: Storm draws (and picks) at the time the host sets; ``loaded`` reports the
    stage's time codes as rtx's does."""
    path = tmp_path / "drive.usda"
    path.write_text(ANIMATED, encoding="utf-8", newline="\n")
    ready = source.wait_event("ready", timeout=30)
    source.send("load", path=str(path))
    loaded = source.wait_event("loaded", timeout=30)
    assert loaded["animated"] is True and loaded["time_codes_per_second"] == 24.0
    assert (loaded["start_time_code"], loaded["end_time_code"]) == (0.0, 48.0)
    wait_health(source, "ok")
    reader = RingReader.open(ready["ring"])
    try:
        look(source, (0, -12, 0), (0, 0, 0))
        # the box's front face is 11 m away, where the view is 16.54 m wide: x = -4 and 4
        # are at NDC -0.484 and 0.484, columns 41 and 118 of 160
        left, right = (-0.484, 0.0), (0.484, 0.0)
        for timecode, there, gone, column in ((0.0, left, right, 41), (48.0, right, left, 118)):
            source.send("set_time", t=timecode / 24, timecode=timecode)
            assert source.call("pick", x=there[0], y=0.0)["path"] == "/World/Box"
            assert source.call("pick", x=gone[0], y=0.0) == {"hit": False}
            f = wait_frame(reader, after=reader.latest().seq + IN_FLIGHT)
            r, g, b, _ = pixel(f, column, H // 2)
            assert r > 2 * g and r > 2 * b, (timecode, (r, g, b))
        assert source.call("state")["timecode"] == 48.0
    finally:
        reader.close()
