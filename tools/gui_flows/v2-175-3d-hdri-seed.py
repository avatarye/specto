"""Seed the project folder for the #175 HDRI flow: scans/spheres.glb, five PBR spheres in
a row (chrome, gold, white, red, blue: glTF metallic-roughness materials, what an
environment lights) and scans/ground.ply, a checkered point cloud under them (points
keep their own colours under any environment). No ``.specto/``: the app initialises
the project on open; the flow submits v2-47-canvas.json (a file tree and a 3D view of
the picked scans).

    uv run python tools/gui_flows/v2-175-3d-hdri-seed.py <folder>
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
HELPERS = HERE.parent.parent / "packages" / "specto-app" / "tests" / "scene3d_helpers.py"


def write_ground(path: Path, half_x: float = 7.0, half_z: float = 4.0, step: float = 0.05) -> int:
    """A flat checkered point grid at y = 0 (binary PLY, x y z float + rgb uchar)."""
    xs = np.arange(-half_x, half_x, step, dtype=np.float32)
    zs = np.arange(-half_z, half_z, step, dtype=np.float32)
    gx, gz = np.meshgrid(xs, zs)
    n = gx.size
    xyz = np.stack([gx.ravel(), np.zeros(n, np.float32), gz.ravel()], axis=1)
    check = ((np.floor(gx.ravel()) + np.floor(gz.ravel())) % 2).astype(bool)
    rgb = np.where(check[:, None], [70, 74, 82], [150, 154, 160]).astype(np.uint8)
    rec = np.empty(
        n, dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")]
    )
    rec["x"], rec["y"], rec["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    rec["r"], rec["g"], rec["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {n}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    path.write_bytes(header.encode("ascii") + rec.tobytes())
    return n


def main() -> int:
    folder = Path(sys.argv[1])
    scans = folder / "scans"
    scans.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("scene3d_helpers", HELPERS)
    assert spec and spec.loader
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    helpers.write_pbr_spheres_glb(scans / "spheres.glb")
    n = write_ground(scans / "ground.ply")
    print(f"[OK] {scans / 'spheres.glb'}, {scans / 'ground.ply'} ({n} points)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
