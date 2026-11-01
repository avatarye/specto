"""Storm on a mesh whose materials are bound to its GeomSubsets (a large depot's buildings'
facades): every view mode draws and the loop never dies. OpenUSD 26.08 fails a TF_VERIFY
per subset in ``HdStMesh::_UpdateDrawItemsForGeomSubsets`` when scene materials go off
(lit to unlit, wireframe or collision); the draw is done by then, so the source notes it
once as a ``warning`` and goes on. Unlit draws each subset in its own material's colour.
Skipped where this machine has no Storm."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from test_usd_source_storm import IN_FLIGHT, look, pixel, wait_frame
from test_usd_source_storm import load as load_storm

from specto_app.frames import control
from specto_core.machine import detect_storm, openusd_prefix

STORM = detect_storm()
pytestmark = pytest.mark.skipif(not STORM.present, reason=STORM.reason)

# a 4 x 2 m wall of two quads facing -Y, the left one bound to red and the right one to
# blue through GeomSubsets, and a collider box below it; seen from 8 m down -Y
STAGE = """#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)

def Xform "World"
{
    def Mesh "Wall" (
        prepend apiSchemas = ["MaterialBindingAPI"]
    )
    {
        int[] faceVertexCounts = [4, 4]
        int[] faceVertexIndices = [0, 1, 4, 3, 1, 2, 5, 4]
        point3f[] points = [(-2, 0, 0), (0, 0, 0), (2, 0, 0), (-2, 0, 2), (0, 0, 2), (2, 0, 2)]
        uniform token subdivisionScheme = "none"
        uniform token subsetFamily:materialBind:familyType = "partition"

        def GeomSubset "Left" (
            prepend apiSchemas = ["MaterialBindingAPI"]
        )
        {
            uniform token elementType = "face"
            uniform token familyName = "materialBind"
            int[] indices = [0]
            rel material:binding = </World/Looks/Red>
        }

        def GeomSubset "Right" (
            prepend apiSchemas = ["MaterialBindingAPI"]
        )
        {
            uniform token elementType = "face"
            uniform token familyName = "materialBind"
            int[] indices = [1]
            rel material:binding = </World/Looks/Blue>
        }
    }

    def Cube "Base" (
        prepend apiSchemas = ["PhysicsCollisionAPI"]
    )
    {
        double size = 1
        color3f[] primvars:displayColor = [(0.5, 0.5, 0.5)]
        double3 xformOp:translate = (0, 0, -1)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Scope "Looks"
    {
        def Material "Red"
        {
            token outputs:surface.connect = </World/Looks/Red/Surface.outputs:surface>

            def Shader "Surface"
            {
                uniform token info:id = "UsdPreviewSurface"
                color3f inputs:diffuseColor = (0.9, 0.1, 0.1)
                token outputs:surface
            }
        }

        def Material "Blue"
        {
            token outputs:surface.connect = </World/Looks/Blue/Surface.outputs:surface>

            def Shader "Surface"
            {
                uniform token info:id = "UsdPreviewSurface"
                color3f inputs:diffuseColor = (0.1, 0.1, 0.9)
                token outputs:surface
            }
        }
    }
}
"""
W, H = 160, 90
# the wall spans x = -2..2, z = 0..2 at 8 m: NDC x = +-0.33, its middle row z = 1 above
# the centre; the left quad's middle at x = -1 m, the right one's at x = +1 m
LEFT, RIGHT = (66, 34), (94, 34)


@pytest.fixture
def stage_file(tmp_path: Path) -> Path:
    path = tmp_path / "subsets.usda"
    path.write_text(STAGE, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def source(tmp_path: Path):
    argv = [sys.executable, "-m", "specto_app.usd.source", "--renderer", "storm"]
    argv += ["--openusd", openusd_prefix(), "--width", str(W), "--height", str(H)]
    env = {**os.environ, "PXR_USD_WINDOWS_DLL_PATH": str(tmp_path)}
    src = control.FrameSourceProcess.spawn(argv, env=env)
    yield src
    src.close()


def frame_after(reader):
    return wait_frame(reader, 30, after=reader.latest().seq + IN_FLIGHT)


def rgb(frame, at) -> tuple[int, int, int]:
    return pixel(frame, *at)[:3]


def warnings(src, wait: float = 0.5) -> list[str]:
    out, deadline = [], time.monotonic() + wait
    while True:
        try:
            out.append(
                src.wait_event("warning", timeout=max(0.01, deadline - time.monotonic()))["message"]
            )
        except Exception:
            return out


def test_every_view_mode_draws_and_the_loop_survives_the_subset_errors(source, stage_file):
    reader = load_storm(source, stage_file)
    try:
        look(source, (0, -8, 1), (0, 0, 1))
        lit = frame_after(reader)
        assert rgb(lit, LEFT)[0] > 2 * rgb(lit, LEFT)[2]  # the red subset, lit
        assert rgb(lit, RIGHT)[2] > 2 * rgb(lit, RIGHT)[0]  # the blue one
        for mode in ("unlit", "lit", "wireframe", "collision", "lit", "unlit", "lit"):
            assert source.call("set_view_mode", mode=mode)["mode"] == mode
            again = frame_after(reader)  # a new frame comes: the loop goes on
            state = source.call("state")
            assert state["failed"] is False and state["view_mode"] == mode
        assert rgb(again, LEFT) == pytest.approx(rgb(lit, LEFT), abs=4)  # lit as before
        assert rgb(again, RIGHT) == pytest.approx(rgb(lit, RIGHT), abs=4)
        # 26.08's verify failures come once, as a warning (none on a fixed OpenUSD)
        said = warnings(source)
        assert len(said) <= 1, said
        assert all("_UpdateDrawItemsForGeomSubsets" in w for w in said), said
        assert source.call("pick", x=-0.15, y=0.25)["path"] == "/World/Wall"
    finally:
        reader.close()


def test_unlit_draws_each_subset_in_its_materials_colour(source, stage_file):
    reader = load_storm(source, stage_file)
    try:
        look(source, (0, -8, 1), (0, 0, 1))
        frame_after(reader)
        source.call("set_view_mode", mode="unlit")
        f = frame_after(reader)
        assert rgb(f, LEFT) == pytest.approx((230, 26, 26), abs=6)
        assert rgb(f, RIGHT) == pytest.approx((26, 26, 230), abs=6)
    finally:
        reader.close()


def test_collision_after_lit_draws_the_collider_and_picks_it(source, stage_file):
    reader = load_storm(source, stage_file)
    try:
        look(source, (0, -8, 1), (0, 0, 1))
        frame_after(reader)
        assert source.call("set_view_mode", mode="collision")["note"] == "1 collider"
        f = frame_after(reader)
        assert max(rgb(f, LEFT)) < 12 and max(rgb(f, RIGHT)) < 12  # the wall is hidden
        # the base's middle (z = -1) is 2 m below the line of sight, where the view is
        # 2 * 8 * tan(0.4) = 6.76 m tall: NDC -2 / 3.38 = -0.59
        assert source.call("pick", x=0.0, y=-0.59)["path"] == "/World/Base"
        assert source.call("state")["failed"] is False
    finally:
        reader.close()
