"""View modes (#153) in ``specto-usd-source``, Storm flavour, on the real Storm: unlit
draws base colours flat (a UsdPreviewSurface's too), wireframe draws edges over
near-black, collision draws only the colliders in a shaded tint and a pick names the
collider. Skipped where this machine has no Storm."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from test_usd_source_storm import IN_FLIGHT, look, pixel, wait_frame
from test_usd_source_storm import load as load_storm

from specto_app.frames import control
from specto_core.machine import detect_storm, openusd_prefix

STORM = detect_storm()
pytestmark = pytest.mark.skipif(not STORM.present, reason=STORM.reason)

# a red box in the middle, a ball with a green UsdPreviewSurface right of it and a blue
# collider wall left of it, seen from 16 m down -Y (x = +-6 m lands at NDC +-0.5)
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

    def Sphere "Ball" (
        prepend apiSchemas = ["MaterialBindingAPI"]
    )
    {
        double radius = 1.5
        rel material:binding = </World/Green>
        double3 xformOp:translate = (6, 0, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Cube "Wall" (
        prepend apiSchemas = ["PhysicsCollisionAPI"]
    )
    {
        double size = 3
        color3f[] primvars:displayColor = [(0.1, 0.1, 0.9)]
        double3 xformOp:translate = (-6, 0, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Sphere "Plain"
    {
        double3 xformOp:translate = (0, 0, 4)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Sphere "Textured" (
        prepend apiSchemas = ["MaterialBindingAPI"]
    )
    {
        rel material:binding = </World/Painted>
        double3 xformOp:translate = (6, 0, 4)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Material "Painted"
    {
        token outputs:surface.connect = </World/Painted/Surface.outputs:surface>

        def Shader "Surface"
        {
            uniform token info:id = "UsdPreviewSurface"
            color3f inputs:diffuseColor.connect = </World/Painted/Image.outputs:rgb>
            token outputs:surface
        }

        def Shader "Image"
        {
            uniform token info:id = "UsdUVTexture"
            asset inputs:file = @missing.png@
            float4 inputs:fallback = (0.9, 0.6, 0.1, 1)
            float3 outputs:rgb
        }
    }

    def Material "Green"
    {
        token outputs:surface.connect = </World/Green/Surface.outputs:surface>

        def Shader "Surface"
        {
            uniform token info:id = "UsdPreviewSurface"
            color3f inputs:diffuseColor = (0.1, 0.8, 0.2)
            token outputs:surface
        }
    }
}
"""
W, H = 160, 90
BOX, BALL, WALL = (80, 45), (120, 45), (40, 45)
PLAIN, TEXTURED = (80, 18), (120, 18)  # the spheres 4 m above the box and the ball
CLEAR = (41, 41, 43)  # Storm's grey behind the stage


@pytest.fixture
def stage_file(tmp_path: Path) -> Path:
    path = tmp_path / "modes.usda"
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


def test_unlit_draws_base_colours_flat(source, stage_file):
    reader = load_storm(source, stage_file)
    try:
        look(source, (0, -16, 0), (0, 0, 0))
        assert source.call("view_modes")["modes"][3]["available"] is True
        answer = source.call("set_view_mode", mode="unlit")
        assert answer == {"mode": "unlit", "note": "UsdPreviewSurface colours, no textures"}
        f = frame_after(reader)
        assert rgb(f, BOX) == pytest.approx((230, 26, 26), abs=6)  # displayColor, unshaded
        assert rgb(f, BALL) == pytest.approx((26, 204, 51), abs=6)  # the preview surface's
        # flat: the ball's rim is as bright as its middle
        assert rgb(f, (BALL[0] + 6, BALL[1] - 4)) == rgb(f, BALL)
    finally:
        reader.close()


def drawn(frame) -> int:
    """How many pixels show something other than the clear colour."""
    return sum(
        1
        for y in range(H)
        for x in range(W)
        if max(abs(a - b) for a, b in zip(rgb(frame, (x, y)), CLEAR, strict=True)) > 6
    )


def test_unlit_keeps_every_gprim_visible(source, stage_file):
    """A gprim with no colour, or a textured preview surface, still draws (grey) unlit:
    nothing the lit view shows goes missing."""
    reader = load_storm(source, stage_file)
    try:
        look(source, (0, -16, 0), (0, 0, 0))
        lit = frame_after(reader)
        source.call("set_view_mode", mode="unlit")
        unlit = frame_after(reader)
        for at in (BOX, BALL, WALL, PLAIN, TEXTURED):
            assert rgb(unlit, at) != pytest.approx(CLEAR, abs=6), at
        assert drawn(unlit) == pytest.approx(drawn(lit), rel=0.05)
    finally:
        reader.close()


def test_wireframe_draws_edges_over_near_black(source, stage_file):
    reader = load_storm(source, stage_file)
    try:
        look(source, (0, -16, 0), (0, 0, 0))
        source.call("set_view_mode", mode="wireframe")
        f = frame_after(reader)
        assert max(rgb(f, (2, 2))) < 12  # near-black behind
        assert max(rgb(f, BOX)) < 12  # the box's face is not filled
        reds = [x for x in range(60, 100) if rgb(f, (x, BOX[1]))[0] > 120]
        assert reds, "the box's edges are drawn in its colour"
        source.call("set_view_mode", mode="lit")
        f = frame_after(reader)
        r, g, _ = rgb(f, BOX)
        assert r > 2 * g and r > 60  # filled and lit again
    finally:
        reader.close()


def test_collision_draws_only_the_colliders_and_a_pick_names_one(source, stage_file):
    reader = load_storm(source, stage_file)
    try:
        look(source, (0, -16, 0), (0, 0, 0))
        assert source.call("set_view_mode", mode="collision") == {
            "mode": "collision",
            "note": "1 collider",
        }
        f = frame_after(reader)
        assert max(rgb(f, BOX)) < 12 and max(rgb(f, BALL)) < 12  # hidden
        r, g, b = rgb(f, WALL)
        assert g > r and g > b and g > 60, (r, g, b)  # the first tint, not the wall's blue
        hit = source.call("pick", x=-0.5, y=0.0)
        assert hit["hit"] is True and hit["path"] == "/World/Wall"
        assert source.call("pick", x=0.0, y=0.0) == {"hit": False}  # the box is hidden
        source.call("set_view_mode", mode="lit")
        f = frame_after(reader)
        r, g, b = rgb(f, BOX)
        assert r > 2 * g and r > 60
        assert source.call("pick", x=0.0, y=0.0)["path"] == "/World/Box"
    finally:
        reader.close()
