"""``specto-usd-source`` rtx flavour against the fake ovrtx (#148): how the inline root
lights a stage. A stage lit only by lights Kit authored (in a layer Kit saved: a Kit
world, the worlds' sky) keeps Kit's fixed exposure; a stage with any other light gets
auto exposure on the viewer camera (plain UsdLux intensities are not on RTX's
photometric scale: a DomeLight or SphereLight at USD's default intensity 1 renders black
under the fixed exposure); a stage without lights gets a default dome light and a note
under the readout."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

from specto_app.frames import control

FAKE = Path(__file__).resolve().parent / "fake_ovrtx"
NO_LIGHTS_NOTE = "no lights in the stage: a default dome light"
AUTO = "bool omni:rtx:autoExposure:enabled = 1"
FALLBACK = 'def DomeLight "Light"'


KIT_META = "    customLayerData = {\n        dictionary renderSettings = {\n        }\n    }\n"


def stage(lights: str = "", kit: bool = False) -> str:
    return f"""#usda 1.0
(
{KIT_META if kit else ""}    defaultPrim = "World"
    upAxis = "Z"
)

def Xform "World"
{{
    def Cube "Box"
    {{
    }}
{lights}
}}
"""


DOME = '    def DomeLight "Dome"\n    {\n    }\n'
SPHERE_MESH_LIGHT = (
    '    def Sphere "Bulb" (\n        prepend apiSchemas = ["MeshLightAPI"]\n    )\n    {\n    }\n'
)


def calls(log: Path, name: str) -> list[dict]:
    if not log.exists():
        return []
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["call"] == name]


def spawn(log: Path) -> control.FrameSourceProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(FAKE), env.get("PYTHONPATH", "")])
    env["FAKE_OVRTX_LOG"] = str(log)
    argv = [sys.executable, "-m", "specto_app.usd.source", "--renderer", "rtx"]
    return control.FrameSourceProcess.spawn([*argv, "--width", "64", "--height", "36"], env=env)


def wait_health(src: control.FrameSourceProcess, state: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        params = src.wait_event("health", timeout=max(0.1, deadline - time.monotonic()))
        if params.get("state") == state:
            return params


def load(tmp_path: Path, text: str, **layers: str) -> tuple[dict, str]:
    """The ``loaded`` event and the inline root ovstage opened for a stage of ``text``;
    ``layers``: more files beside it, by stem."""
    for stem, layer in layers.items():
        (tmp_path / f"{stem}.usda").write_text(layer, encoding="utf-8", newline="\n")
    path = tmp_path / "stage.usda"
    path.write_text(text, encoding="utf-8", newline="\n")
    log = tmp_path / "ovrtx.log"
    src = spawn(log)
    try:
        src.wait_event("ready")
        src.send("load", path=str(path))
        loaded = src.wait_event("loaded", timeout=30)
        wait_health(src, "ok")
    finally:
        src.close()
    (root,) = calls(log, "open_usd_from_string")
    return loaded, root["text"]


def camera_of(root: str) -> str:
    return root.split('def Camera "Camera"', 1)[1].split("}", 1)[0]


def test_a_stage_without_lights_gets_a_default_dome_and_says_so(tmp_path):
    loaded, root = load(tmp_path, stage())
    assert FALLBACK in root
    assert "float inputs:intensity = 1000" in root
    assert AUTO in camera_of(root)
    assert loaded["renderer_note"] == NO_LIGHTS_NOTE


def test_a_plain_usdlux_stage_gets_auto_exposure_and_no_dome(tmp_path):
    loaded, root = load(tmp_path, stage(DOME))
    camera = camera_of(root)
    assert AUTO in camera
    # unclamped: the histogram's default floor (50 nits) keeps a unit dome black;
    # averaged: the median of a frame mostly background burns out what is lit
    assert "bool omni:rtx:autoExposure:histogramClamping:enabled = 0" in camera
    assert 'token omni:rtx:autoExposure:histogramFilter = "average"' in camera
    assert FALLBACK not in root
    assert "renderer_note" not in loaded
    # nothing is authored over the stage's own prims
    assert "/World" not in root and "over " not in root


def test_a_mesh_light_counts_as_a_light(tmp_path):
    loaded, root = load(tmp_path, stage(SPHERE_MESH_LIGHT))
    assert FALLBACK not in root and "renderer_note" not in loaded


def test_a_kit_stage_with_its_own_lights_keeps_the_fixed_exposure(tmp_path):
    loaded, root = load(tmp_path, stage(DOME, kit=True))
    assert "autoExposure" not in root
    assert FALLBACK not in root and "renderer_note" not in loaded


def test_a_kit_stage_without_lights_gets_the_dome_and_auto_exposure(tmp_path):
    loaded, root = load(tmp_path, stage(kit=True))
    assert FALLBACK in root and AUTO in camera_of(root)
    assert loaded["renderer_note"] == NO_LIGHTS_NOTE


#: a sky Kit saved, as the worlds reference it
KIT_SKY = f"""#usda 1.0
(
{KIT_META}    defaultPrim = "Sky"
)

def Xform "Sky"
{{
{DOME}}}
"""
SKY_REF = """    def Xform "Sky" (
        prepend references = @./sky.usda@
    )
    {
    }
"""


def test_a_plain_stage_lit_by_a_referenced_kit_sky_keeps_the_fixed_exposure(tmp_path):
    loaded, root = load(tmp_path, stage(SKY_REF), sky=KIT_SKY)
    assert "autoExposure" not in root
    assert FALLBACK not in root and "renderer_note" not in loaded


def test_a_plain_light_beside_a_kit_sky_gets_auto_exposure(tmp_path):
    sun = """    def DistantLight "Sun"
    {
    }
"""
    loaded, root = load(tmp_path, stage(SKY_REF + sun), sky=KIT_SKY)
    assert "renderer_note" not in loaded
    assert AUTO in camera_of(root)
    assert FALLBACK not in root
