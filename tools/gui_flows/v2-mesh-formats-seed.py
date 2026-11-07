"""Seed the project folder for the SPEC-42 mesh-format flows: robot/ holds a Z-up box as
an STL (link.stl), an OBJ with its MTL and a green texture outside the OBJ's folder
(panel.obj, panel.mtl naming ../shared/panel.png) and a COLLADA file in centimetres,
Z_UP, with a blue box and a red textured box 80 cm above it (arm.dae, textures/arm.png).
The files come from the readers tests' synthetic writers
(``packages/specto-readers/tests/mesh_samples.py``). No ``.specto/``: the app
initialises the project on open.

    uv run python tools/gui_flows/v2-mesh-formats-seed.py <folder>
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[2] / "packages/specto-readers/tests/mesh_samples.py"


def main() -> int:
    robot = Path(sys.argv[1]) / "robot"
    robot.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("mesh_samples", SAMPLES)
    assert spec and spec.loader
    samples = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(samples)
    samples.write_stl(robot / "link.stl")
    # the texture beside the robot folder, as robot packages keep them: ../shared/
    samples.write_obj(robot / "panel.obj", texture_ref="../shared/panel.png")
    samples.write_dae(robot / "arm.dae")
    print(f"[OK] {robot}: link.stl, panel.obj (+ mtl, ../shared texture), arm.dae (+ texture)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
