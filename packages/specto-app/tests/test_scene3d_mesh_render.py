"""SPEC-42, the web pixel check: the real 3D page in headless Qt WebEngine
(``scene3d_render_child.py``) loads each robot-mesh format the way the host hands it over
and draws it: an STL (Babylon's STL loader, the plain grey material), an OBJ with its MTL
and texture next to it (the green comes from the texture) and a DAE as the Y-up metre GLB
resolve converted it to (blue below, the red textured box 80 cm above). The synthetic
files are Z-up: with the view's up param z the STL and OBJ stand upright, with y they lie
along z; the DAE declares its up axis and stands upright either way. Needs the vendored
Babylon and a WebGL2 context; skipped without them."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from scene3d_helpers import mesh_samples

import specto_web
from specto_readers import dae
from specto_readers.scene3d import prepare_scene

CHILD = Path(__file__).with_name("scene3d_render_child.py")
WEB = specto_web.find_web_root()

pytestmark = pytest.mark.skipif(
    WEB is None or not (WEB / "vendor" / "babylon" / "babylon.js").is_file(),
    reason="Babylon is not vendored: " + specto_web.BUILD_HINT,
)

#: straight at the boxes from the front, the canvas centre 0.6 m up
CAMERA = {
    "position": [0.0, 0.6, 3.2],
    "target": [0.0, 0.6, 0.0],
    "up": [0.0, 1.0, 0.0],
    "fov_y": 0.8,
    "near": 0.01,
    "far": 100.0,
}
UPRIGHT = [[-0.2, 0.0, -0.2], [0.2, 0.8, 0.2]]  # the Z-up box, turned to stand in y
LYING = [[-0.2, -0.2, 0.0], [0.2, 0.2, 0.8]]  # the same numbers taken as they are
DAE_UPRIGHT = [[-0.2, 0.0, -0.2], [0.2, 1.2, 0.2]]
BACKGROUND = (250, 247, 241)  # --color-surface-alt


def render(tmp: Path, entry: dict | list[dict], more: tuple[dict, ...] = ()) -> dict:
    plan = {
        "entries": entry if isinstance(entry, list) else [entry],
        "out": str(tmp / "out"),
        "camera": CAMERA,
        "params": {"up": "z"},
        "steps": [{"environment": "none"}, {"up": "y"}, *more],
    }
    res = subprocess.run(
        [sys.executable, str(CHILD), json.dumps(plan)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    line = next((x for x in res.stdout.splitlines() if x.startswith("RESULT ")), None)
    said = f"{res.stdout[-3000:]}\n{res.stderr[-3000:]}"
    assert line, f"no result from the child (rc {res.returncode})\n{said}"
    out = json.loads(line[len("RESULT ") :])
    if "WebGL" in str(out.get("error", "")):
        pytest.skip(f"no WebGL2 here: {out['error']}")
    assert "error" not in out, f"{out['error']}\n{said}"
    return out


def pixel(step: dict, fx: float, fy: float) -> tuple[int, int, int]:
    """The grab's colour at a fraction of the 3D canvas."""
    img = QImage(step["png"])
    x, y, w, h = step["report"]["canvas"]
    c = img.pixelColor(x + int(w * fx), y + int(h * fy))
    return c.red(), c.green(), c.blue()


def bounds(step: dict, item: str = "m") -> list[list[float]]:
    return step["report"]["items"][item]["bounds"]


def painted(rgb: tuple[int, int, int]) -> bool:
    return sum(abs(a - b) for a, b in zip(rgb, BACKGROUND, strict=True)) > 60


def test_an_stl_draws_grey_and_follows_the_up_param(tmp_path: Path):
    stl = mesh_samples().write_stl(tmp_path / "link.stl")
    entry = {"id": "m", "kind": "mesh", "file": str(stl), "ext": ".stl"}
    out = render(tmp_path, entry, ({"up": "z", "mesh_scale": 0.01},))  # mesh_unit cm
    z_up, y_up, in_cm = out["steps"]
    assert bounds(z_up) == UPRIGHT and bounds(y_up) == LYING
    # mesh_unit cm: the numbers are centimetres, the box 8 mm tall (the page rounds to cm)
    assert bounds(in_cm) == [[-0.0, 0.0, -0.0], [0.0, 0.01, 0.0]]
    r, g, b = pixel(z_up, 0.5, 0.5)
    assert painted((r, g, b)) and max(r, g, b) - min(r, g, b) < 24 and max(r, g, b) < 200


def test_an_obj_draws_its_mtl_texture_and_follows_the_up_param(tmp_path: Path):
    obj = mesh_samples().write_obj(tmp_path / "panel.obj")
    out = render(tmp_path, {"id": "m", "kind": "mesh", "file": str(obj), "ext": ".obj"})
    z_up, y_up = out["steps"][:2]
    assert bounds(z_up) == UPRIGHT and bounds(y_up) == LYING
    r, g, b = pixel(z_up, 0.5, 0.5)
    assert g > r + 60 and g > b + 60, f"not the green texture: {(r, g, b)}"


def test_an_obj_texture_with_map_options_still_loads(tmp_path: Path):
    """``map_Kd -s 1 1 1 -clamp on textures/panel.png``: the page strips the options before
    Babylon's MTL loader builds the texture URL (it only knows map_bump's -bm)."""
    obj = mesh_samples().write_obj(tmp_path / "panel.obj")
    mtl = tmp_path / "panel.mtl"
    text = mtl.read_text(encoding="ascii").replace("map_Kd ", "map_Kd -s 1 1 1 -clamp on ")
    mtl.write_text(text, encoding="ascii")
    out = render(tmp_path, {"id": "m", "kind": "mesh", "file": str(obj), "ext": ".obj"})
    r, g, b = pixel(out["steps"][0], 0.5, 0.5)
    assert g > r + 60 and g > b + 60, f"not the green texture: {(r, g, b)}"


def test_a_dae_draws_through_its_glb_upright_whatever_the_up_param(tmp_path: Path):
    samples = mesh_samples()
    src = samples.write_dae(tmp_path / "robot.dae")
    glb = dae.converted_path(src)
    dae.convert(src, glb)
    entry = {"id": "m", "kind": "mesh", "file": str(glb), "ext": ".glb", "up": "y"}
    out = render(tmp_path, entry)
    z_up, y_up = out["steps"][:2]
    assert bounds(z_up) == DAE_UPRIGHT and bounds(y_up) == DAE_UPRIGHT
    # the blue box from 0 to 40 cm, the textured red one from 80 to 120 cm
    r, g, b = pixel(z_up, 0.5, 0.65)
    assert b > r + 60, f"not the blue material: {(r, g, b)}"
    r, g, b = pixel(z_up, 0.5, 0.35)
    assert r > b + 60 and r > g + 60, f"not the red texture: {(r, g, b)}"
    assert not painted(pixel(z_up, 0.5, 0.5))  # the gap between them


def entry_from_scene(item: str, path: Path, scene: dict) -> dict:
    """The page's entry for a prepared file, as the host builds it: a derived GLB when
    resolve wrote one, the file itself otherwise, and the file's own up axis if it has one."""
    glb = (scene.get("files") or {}).get("glb")
    entry = {"id": item, "kind": scene["kind"], "file": glb or str(path)}
    entry["ext"] = ".glb" if glb else scene["role"]
    if scene.get("up"):
        entry["up"] = scene["up"]
    return entry


def test_a_mixed_scene_lines_up_under_up_y_and_up_z(tmp_path: Path):
    """SPEC-50: a Z-up STL, a Z_UP DAE and a glTF (Y up by its spec) in one scene. The STL
    has no up axis of its own and follows the view's up param; the DAE converted to a Y-up
    GLB and the glTF both declare theirs, so under up z the STL stands up beside them and
    under up y it lies along z, while the two Y-up models stand the same in both."""
    samples = mesh_samples()
    stl = samples.write_stl(tmp_path / "link.stl")
    src = samples.write_dae(tmp_path / "robot.dae")
    cache = tmp_path / "cache"
    robot = prepare_scene(src, cache)
    gltf = tmp_path / "model.glb"  # the same Y-up model, as a plain glTF file
    gltf.write_bytes(Path(robot["files"]["glb"]).read_bytes())
    entries = [
        entry_from_scene("stl", stl, prepare_scene(stl, cache)),
        entry_from_scene("dae", src, robot),
        entry_from_scene("gltf", gltf, prepare_scene(gltf, cache)),
    ]
    out = render(tmp_path, entries)
    z_up, y_up = out["steps"][:2]
    assert bounds(z_up, "stl") == UPRIGHT and bounds(y_up, "stl") == LYING
    for step in (z_up, y_up):
        assert bounds(step, "dae") == DAE_UPRIGHT
        assert bounds(step, "gltf") == bounds(step, "dae"), "the glTF and the DAE disagree"
