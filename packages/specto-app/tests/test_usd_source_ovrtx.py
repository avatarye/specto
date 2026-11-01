"""``specto-usd-source`` with the real ovrtx (#137) on a large world from the user's
depot (read only; ``SPECTO_P4_DEPOT`` and ``SPECTO_P4_WORLD``): the stage loads with its
metrics, RT2 renders a non-black frame, and camera cuts and resizes keep frames coming.
Skipped without the rtx extra or the file. A warm shader cache takes about 30 s; a cold
one about 3 minutes."""

from __future__ import annotations

import importlib.util
import math
import os
import sys
import time
from pathlib import Path

import pytest

# find_spec, not importorskip: the test process itself never loads ovrtx (#135)
if importlib.util.find_spec("ovrtx") is None:
    pytest.skip("needs the rtx extra: uv sync --extra rtx", allow_module_level=True)

from specto_app.frames import control
from specto_app.frames.ring import RingReader

pytestmark = pytest.mark.ovrtx


def perforce_depot() -> Path:
    """The user's large USD depot (read only): ``SPECTO_P4_DEPOT``, else ``~/depot``.
    Nothing here names a real depot; the world and sky files are environment too."""
    return Path(os.environ.get("SPECTO_P4_DEPOT") or (Path.home() / "depot"))


#: a world in the depot, relative to its root (``SPECTO_P4_WORLD``): a Z-up, metre stage
WORLD = perforce_depot() / os.environ.get("SPECTO_P4_WORLD", "worlds/example_world.usda")
FIRST_FRAME_S = 400  # a cold shader cache compiles for about 2.5 minutes, then the scene


def wait_health(src, state: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        params = src.wait_event("health", timeout=max(0.1, deadline - time.monotonic()))
        if params.get("state") == "error":
            raise AssertionError(f"the source failed: {params}")
        if params.get("state") == state:
            return params


def wait_frame(reader: RingReader, timeout: float, after: int = 0, **want):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        f = reader.latest()
        if f is not None and f.seq > after and all(getattr(f, k) == v for k, v in want.items()):
            return f
        time.sleep(0.01)
    raise AssertionError(f"no frame with {want}")


def mean_rgb(frame) -> float:
    px = frame.pixels
    rgb = [v for i, v in enumerate(px) if i % 4 != 3]
    return sum(rgb) / len(rgb)


@pytest.mark.skipif(not WORLD.is_file(), reason="no depot world here (SPECTO_P4_WORLD)")
def test_rtx_renders_the_depot_world():
    src = control.FrameSourceProcess.spawn(
        [
            sys.executable,
            "-m",
            "specto_app.usd.source",
            "--renderer",
            "rtx",
            "--width",
            "640",
            "--height",
            "360",
        ]
    )
    try:
        ready = src.wait_event("ready", timeout=30)
        src.send("load", path=str(WORLD))
        loaded = src.wait_event("loaded", timeout=FIRST_FRAME_S)
        assert loaded["renderer"] == "rtx"
        assert loaded["up_axis"] == "Z" and loaded["meters_per_unit"] == 1.0
        # a Kit world: a range of -1 .. 0 and nothing time-sampled, so no transport (#141)
        assert loaded["animated"] is False
        assert (loaded["start_time_code"], loaded["end_time_code"]) == (-1.0, 0.0)
        lo, hi = loaded["bounds"]  # a finite box with some extent on every axis
        assert all(math.isfinite(v) for v in (*lo, *hi))
        assert all(h > lo_ for lo_, h in zip(lo, hi, strict=True))
        wait_health(src, "ok", FIRST_FRAME_S)
        reader = RingReader.open(ready["ring"])
        try:
            f = wait_frame(reader, 60, width=640, height=360)
            assert mean_rgb(f) > 20, "the first frame is black"
            # a cut to a second viewpoint: the history resets and frames keep coming
            src.send(
                "set_camera",
                position=[-12.0, 9.0, 4.0],
                target=[23.0, -17.0, 2.0],
                up=[0.0, 0.0, 1.0],
                fov_y=0.8,
                near=0.05,
                far=5000.0,
                cut=True,
            )
            f = wait_frame(reader, 60, after=f.seq + 2)
            assert mean_rgb(f) > 20
            assert src.call("resize", width=320, height=180) == {"width": 320, "height": 180}
            f = wait_frame(reader, 60, width=320, height=180)
            assert mean_rgb(f) > 20
        finally:
            reader.close()
    finally:
        assert src.close(timeout=60) == 0


#: a dome-light sky layer in the depot, relative to its root (``SPECTO_P4_SKY``)
SKY = perforce_depot() / os.environ.get("SPECTO_P4_SKY", "skies/example_sky.usd")
#: a red box that crosses a grey ground along +X in two seconds (48 timecodes at 24 per
#: second), lit by the depot's sky
ANIMATED = """#usda 1.0
(
    defaultPrim = "World"
    endTimeCode = 48
    metersPerUnit = 1
    startTimeCode = 0
    timeCodesPerSecond = 24
    upAxis = "Z"
)

def Xform "World"
{
    def Xform "Sky" (
        prepend references = @SKY@
    )
    {
    }

    def Mesh "Ground"
    {
        int[] faceVertexCounts = [4]
        int[] faceVertexIndices = [0, 1, 2, 3]
        normal3f[] normals = [(0, 0, 1), (0, 0, 1), (0, 0, 1), (0, 0, 1)]
        point3f[] points = [(-10, -10, 0), (10, -10, 0), (10, 10, 0), (-10, 10, 0)]
        color3f[] primvars:displayColor = [(0.5, 0.5, 0.5)]
    }

    def Cube "Box"
    {
        double size = 2
        color3f[] primvars:displayColor = [(1, 0.1, 0.1)]
        double3 xformOp:translate.timeSamples = {
            0: (-6, 0, 1),
            48: (6, 0, 1),
        }
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
}
"""


def red_x(frame) -> float:
    """The red box's mean column in a frame."""
    import numpy as np

    px = np.frombuffer(bytes(frame.pixels), np.uint8).reshape(frame.height, frame.stride // 4, 4)
    rgb = px[:, : frame.width, :3].astype(int)
    xs = np.nonzero(rgb[..., 0] > rgb[..., 1] + 60)[1]
    assert xs.size, "no red box in the frame"
    return float(xs.mean())


@pytest.mark.skipif(not SKY.is_file(), reason="no depot sky here (SPECTO_P4_SKY)")
def test_rtx_moves_an_animated_stage_with_set_time(tmp_path: Path):
    """#141: ``set_time`` re-evaluates the time samples (USD time, in seconds) on the real
    renderer: the box is left of centre at timecode 0, right of it at 48, and back."""
    stage = tmp_path / "drive.usda"
    stage.write_text(ANIMATED.replace("SKY", SKY.as_posix()), encoding="utf-8")
    src = control.FrameSourceProcess.spawn(
        [
            sys.executable,
            "-m",
            "specto_app.usd.source",
            "--renderer",
            "rtx",
            "--width",
            "640",
            "--height",
            "360",
        ]
    )
    try:
        ready = src.wait_event("ready", timeout=30)
        src.send("load", path=str(stage))
        loaded = src.wait_event("loaded", timeout=FIRST_FRAME_S)
        assert loaded["animated"] is True and loaded["time_codes_per_second"] == 24.0
        wait_health(src, "ok", FIRST_FRAME_S)
        src.send(
            "set_camera",
            position=[0.0, -24.0, 12.0],
            target=[0.0, 0.0, 0.0],
            up=[0.0, 0.0, 1.0],
            fov_y=0.8,
            near=0.05,
            far=500.0,
            cut=True,
        )
        reader = RingReader.open(ready["ring"])
        try:
            xs = []
            for timecode in (0.0, 48.0, 24.0):
                src.send("set_time", t=timecode / 24, timecode=timecode)
                f = wait_frame(reader, 60)
                f = wait_frame(reader, 60, after=f.seq + 4)  # past the frames in flight
                xs.append(red_x(f))
            left, right, middle = xs
            assert left < 320 - 60 and right > 320 + 60, xs
            assert abs(middle - 320) < 30, xs
        finally:
            reader.close()
    finally:
        assert src.close(timeout=60) == 0
