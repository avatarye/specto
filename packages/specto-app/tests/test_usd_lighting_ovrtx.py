"""``specto-usd-source`` with the real ovrtx (#148): stages lit only by plain UsdLux lights
at USD's default intensity (a DomeLight, a SphereLight, a DistantLight) and a stage with
no lights render lit, neither black nor burnt out. Skipped without the rtx extra; a warm
shader cache takes about 30 s, a cold one about 3 minutes."""

from __future__ import annotations

import importlib.util
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

FIRST_FRAME_S = 400  # a cold shader cache compiles for about 2.5 minutes, then the scene
SPHERE_AT = (
    "        double3 xformOp:translate = (3, -3, 5)\n"
    '        uniform token[] xformOpOrder = ["xformOp:translate"]\n'
)
SUN_AT = (
    "        float3 xformOp:rotateXYZ = (-45, 0, 30)\n"
    '        uniform token[] xformOpOrder = ["xformOp:rotateXYZ"]\n'
)
LIGHTS = {
    "dome": '    def DomeLight "Dome"\n    {\n    }\n',
    "sphere": f'    def SphereLight "Bulb"\n    {{\n{SPHERE_AT}    }}\n',
    "distant": f'    def DistantLight "Sun"\n    {{\n{SUN_AT}    }}\n',
    "none": "",
}


def stage(lights: str) -> str:
    """A red box on a grey ground, lit by ``lights`` alone (USD's default intensities)."""
    return f"""#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)

def Xform "World"
{{
{lights}
    def Mesh "Ground"
    {{
        int[] faceVertexCounts = [4]
        int[] faceVertexIndices = [0, 1, 2, 3]
        normal3f[] normals = [(0, 0, 1), (0, 0, 1), (0, 0, 1), (0, 0, 1)]
        point3f[] points = [(-10, -10, 0), (10, -10, 0), (10, 10, 0), (-10, 10, 0)]
        color3f[] primvars:displayColor = [(0.5, 0.5, 0.5)]
    }}

    def Cube "Box"
    {{
        double size = 2
        color3f[] primvars:displayColor = [(1, 0.1, 0.1)]
        double3 xformOp:translate = (0, 0, 1)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }}
}}
"""


def wait_health(src, state: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        params = src.wait_event("health", timeout=max(0.1, deadline - time.monotonic()))
        if params.get("state") == "error":
            raise AssertionError(f"the source failed: {params}")
        if params.get("state") == state:
            return params


def wait_frame(reader: RingReader, timeout: float, after: int = 0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        f = reader.latest()
        if f is not None and f.seq > after:
            return f
        time.sleep(0.01)
    raise AssertionError("no frame")


def lit_and_burnt(frame) -> tuple[float, float]:
    """The share of pixels lit (a channel above 40) and of those burnt out (all 255)."""
    import numpy as np

    px = np.frombuffer(bytes(frame.pixels), np.uint8).reshape(frame.height, frame.stride // 4, 4)
    rgb = px[:, : frame.width, :3]
    lit = (rgb.max(axis=-1) > 40).mean()
    burnt = (rgb.min(axis=-1) == 255).mean()
    return round(float(lit), 3), round(float(burnt), 3)


def test_plain_usdlux_stages_and_a_stage_without_lights_render_lit(tmp_path: Path):
    src = control.FrameSourceProcess.spawn(
        [sys.executable, "-m", "specto_app.usd.source", "--renderer", "rtx",
         "--width", "320", "--height", "180"]
    )  # fmt: skip
    try:
        ready = src.wait_event("ready", timeout=30)
        reader = RingReader.open(ready["ring"])
        try:
            shares, notes = {}, {}
            for name, lights in LIGHTS.items():
                path = tmp_path / f"{name}.usda"
                path.write_text(stage(lights), encoding="utf-8", newline="\n")
                src.send("load", path=str(path))
                notes[name] = src.wait_event("loaded", timeout=FIRST_FRAME_S).get(
                    "renderer_note", ""
                )
                ok = wait_health(src, "ok", FIRST_FRAME_S)
                f = wait_frame(reader, 60, after=ok["seq"] + 30)  # auto exposure settles
                shares[name] = lit_and_burnt(f)
            # before #148 the dome, the sphere and no lights were black (no pixel lit)
            assert all(lit > 0.05 and burnt < 0.2 for lit, burnt in shares.values()), shares
            assert notes == {
                "dome": "", "sphere": "", "distant": "",
                "none": "no lights in the stage: a default dome light",
            }  # fmt: skip
        finally:
            reader.close()
    finally:
        assert src.close(timeout=60) == 0
