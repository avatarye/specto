"""View modes (#153) in ``specto-usd-source`` on the real ovrtx: one small sky-lit stage
through lit, unlit (albedo: flat base colour, black behind), wireframe (wires in their
colour over black), collision (only the collider, in its tint; a pick names it) and back
to lit, all without a reload. Skipped without the rtx extra or the depot sky."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

if importlib.util.find_spec("ovrtx") is None:
    pytest.skip("needs the rtx extra: uv sync --extra rtx", allow_module_level=True)

from test_usd_source_ovrtx import FIRST_FRAME_S, SKY, wait_frame, wait_health

from specto_app.frames import control
from specto_app.frames.ring import RingReader

pytestmark = pytest.mark.ovrtx

# a red box in the middle and a blue collider wall left of it on a grey ground, lit by
# the worlds' sky
STAGE = """#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
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
        point3f[] points = [(-20, -20, -1), (20, -20, -1), (20, 20, -1), (-20, 20, -1)]
        color3f[] primvars:displayColor = [(0.5, 0.5, 0.5)]
    }

    def Cube "Box"
    {
        double size = 2
        color3f[] primvars:displayColor = [(0.9, 0.1, 0.1)]
    }

    def Cube "Wall" (
        prepend apiSchemas = ["PhysicsCollisionAPI"]
    )
    {
        double size = 3
        color3f[] primvars:displayColor = [(0.1, 0.1, 0.9)]
        double3 xformOp:translate = (-6, 0, 0.5)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
}
"""
W, H = 320, 180
BOX, WALL, SKY_PX = (160, 90), (80, 90), (160, 8)


def rgb(frame, at) -> tuple[int, int, int]:
    x, y = at
    i = y * frame.stride + x * 4
    return tuple(frame.pixels[i : i + 3])


@pytest.mark.skipif(not SKY.is_file(), reason="no depot sky here (SPECTO_P4_SKY)")
def test_rtx_switches_view_modes_without_a_reload(tmp_path: Path):
    stage = tmp_path / "modes.usda"
    stage.write_text(STAGE.replace("SKY", SKY.as_posix()), encoding="utf-8")
    src = control.FrameSourceProcess.spawn(
        [sys.executable, "-m", "specto_app.usd.source", "--renderer", "rtx",
         "--width", str(W), "--height", str(H)]
    )  # fmt: skip
    try:
        ready = src.wait_event("ready", timeout=30)
        src.send("load", path=str(stage))
        wait_health(src, "ok", FIRST_FRAME_S)
        src.send(
            "set_camera", position=[0.0, -16.0, 0.0], target=[0.0, 0.0, 0.0],
            up=[0.0, 0.0, 1.0], fov_y=0.8, near=0.05, far=500.0, cut=True,
        )  # fmt: skip
        reader = RingReader.open(ready["ring"])

        def settled():
            f = wait_frame(reader, 60)
            return wait_frame(reader, 60, after=f.seq + 8)  # past the frames in flight

        try:
            lit = settled()
            assert sum(rgb(lit, SKY_PX)) > 300  # the sky behind

            assert src.call("set_view_mode", mode="unlit")["mode"] == "unlit"
            f = settled()
            r, g, b = rgb(f, BOX)
            assert r > 150 and g < 60 and b < 60, (r, g, b)  # the box's base colour
            assert max(rgb(f, SKY_PX)) < 10  # black behind, no sky

            src.call("set_view_mode", mode="wireframe")
            f = settled()
            assert max(rgb(f, SKY_PX)) < 10
            row = [rgb(f, (x, BOX[1])) for x in range(120, 200)]
            assert any(p[0] > 100 and p[0] > 2 * p[2] for p in row), "red wires"
            assert sum(1 for p in row if max(p) < 20) > len(row) // 2  # mostly unfilled

            answer = src.call("set_view_mode", mode="collision")
            assert answer == {"mode": "collision", "note": "1 collider"}
            f = settled()
            assert max(rgb(f, BOX)) < 10  # hidden
            r, g, b = rgb(f, WALL)
            assert g > r and g > b and g > 60, (r, g, b)  # the tint, not the wall's blue
            hit = src.call("pick", x=-0.5, y=0.0)
            assert hit["hit"] is True and hit["path"] == "/World/Wall"

            src.call("set_view_mode", mode="lit")
            f = settled()
            assert sum(rgb(f, SKY_PX)) > 300
            r, g, b = rgb(f, BOX)
            assert r > g and r > b, (r, g, b)
        finally:
            reader.close()
    finally:
        assert src.close(timeout=60) == 0
