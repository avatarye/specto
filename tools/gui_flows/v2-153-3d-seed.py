"""Seed the project folder for the #153 view-mode flow: the #47 scene (v2-47-seed.py,
smaller) plus scans/crate.gltf, a box with an Unreal collision hull beside it
(``UCX_Crate_00``, a slightly larger box), so the 3D view offers the collision mode.

    uv run python tools/gui_flows/v2-153-3d-seed.py <folder>
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def box(lo: tuple[float, float, float], hi: tuple[float, float, float]):
    """A box's 24 vertices (4 per face, flat normals) and 36 indices."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    faces = [
        ((1, 0, 0), [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]),
        ((-1, 0, 0), [(x0, y0, z1), (x0, y1, z1), (x0, y1, z0), (x0, y0, z0)]),
        ((0, 1, 0), [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)]),
        ((0, -1, 0), [(x0, y0, z1), (x0, y0, z0), (x1, y0, z0), (x1, y0, z1)]),
        ((0, 0, 1), [(x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (x0, y0, z1)]),
        ((0, 0, -1), [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)]),
    ]
    pos = np.array([v for _, vs in faces for v in vs], np.float32)
    nrm = np.array([n for n, vs in faces for _ in vs], np.float32)
    idx = np.array([[4 * f, 4 * f + 1, 4 * f + 2, 4 * f, 4 * f + 2, 4 * f + 3] for f in range(6)], np.uint16)
    return pos, nrm, idx.ravel()


def write_gltf(path: Path) -> None:
    parts = [("Crate", box((-6, 0, -6), (6, 12, 6)), 0), ("UCX_Crate_00", box((-7, -1, -7), (7, 13, 7)), 1)]
    blob = b""
    views, accessors, meshes, nodes = [], [], [], []
    for name, (pos, nrm, idx), mat in parts:
        first = len(accessors)
        for arr, target, kind, comp in ((pos, 34962, "VEC3", 5126), (nrm, 34962, "VEC3", 5126), (idx, 34963, "SCALAR", 5123)):
            data = arr.tobytes()
            views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target})
            acc = {"bufferView": len(views) - 1, "componentType": comp, "count": len(arr), "type": kind}
            if kind == "VEC3" and arr is pos:
                acc["min"], acc["max"] = pos.min(0).tolist(), pos.max(0).tolist()
            accessors.append(acc)
            blob += data + b"\0" * (-len(data) % 4)
        meshes.append({"name": name, "primitives": [{"attributes": {"POSITION": first, "NORMAL": first + 1}, "indices": first + 2, "material": mat}]})
        nodes.append({"name": name, "mesh": len(meshes) - 1, "translation": [0, 6, 40]})
    gltf = {
        "asset": {"version": "2.0", "generator": "specto v2-153-3d-seed"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": [
            {"name": "crate", "pbrMetallicRoughness": {"baseColorFactor": [0.75, 0.45, 0.2, 1], "metallicFactor": 0, "roughnessFactor": 0.7}},
            {"name": "hull", "pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1]}},
        ],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob), "uri": "data:application/octet-stream;base64," + base64.b64encode(blob).decode("ascii")}],
    }
    path.write_text(json.dumps(gltf), encoding="utf-8")


def main() -> int:
    folder = Path(sys.argv[1])
    subprocess.run(
        [sys.executable, str(HERE / "v2-47-seed.py"), str(folder), "--points", "200000", "--splats", "50000"],
        check=True,
    )
    write_gltf(folder / "scans" / "crate.gltf")
    print(f"[OK] {folder / 'scans' / 'crate.gltf'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
