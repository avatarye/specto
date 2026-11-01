"""Seed the project folder for the v2 #47 scripted flow: synthetic 3D files under
scans/ (the repo's test data has none), plus notes/. No ``.specto/``: the app
initialises the project on open.

    uv run python tools/gui_flows/v2-47-seed.py <folder> [--points 1000000] [--splats 200000]

- scans/terrain.ply   binary little-endian PLY point cloud (x y z float, rgb uchar): a
                      noisy terrain with boxes, coloured by height (the research spike's
                      cloud, so numbers compare with docs/research/babylon-3d-view.md)
- scans/torus.ply     binary PLY mesh with normals, colours and quad faces (the resolve
                      step fans quads and writes a GLB)
- scans/cloud.splat   antimatter15 .splat (32 B/splat) over the same terrain, beside it

``--points 20000000 --splats 2000000`` makes the acceptance-criteria scene.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import numpy as np

RNG = np.random.default_rng(31)


def cloud(n: int) -> tuple[np.ndarray, np.ndarray]:
    """n points: 80 % terrain over 200 m x 200 m, 20 % on box surfaces; colours by height."""
    nt = int(n * 0.8)
    x = RNG.uniform(-100, 100, nt).astype(np.float32)
    z = RNG.uniform(-100, 100, nt).astype(np.float32)
    y = (4 * np.sin(x / 13) * np.cos(z / 17) + RNG.normal(0, 0.05, nt)).astype(np.float32)
    nb = n - nt
    bx = RNG.integers(-8, 8, nb) * 12.0
    bz = RNG.integers(-8, 8, nb) * 12.0
    h = 6 + (np.abs(bx * 7 + bz * 3) % 20)
    face = RNG.integers(0, 5, nb)
    u = RNG.uniform(-4, 4, nb)
    v = RNG.uniform(0, 1, nb) * h
    px = np.where(face == 0, -4, np.where(face == 1, 4, u))
    pz = np.where(face == 2, -4, np.where(face == 3, 4, np.where(face == 4, u[::-1], u)))
    py = np.where(face == 4, h, v)
    pts = np.empty((n, 3), np.float32)
    pts[:nt, 0], pts[:nt, 1], pts[:nt, 2] = x, y, z
    pts[nt:, 0], pts[nt:, 1], pts[nt:, 2] = bx + px, py, bz + pz
    t = np.clip((pts[:, 1] + 5) / 30, 0, 1)
    rgb = np.empty((n, 3), np.uint8)
    rgb[:, 0] = (60 + 190 * t).astype(np.uint8)
    rgb[:, 1] = (140 - 60 * t).astype(np.uint8)
    rgb[:, 2] = (90 + 120 * (1 - t)).astype(np.uint8)
    return pts, rgb


def write_ply_points(path: Path, pts: np.ndarray, rgb: np.ndarray) -> None:
    n = len(pts)
    rec = np.empty(
        n, dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")]
    )
    rec["x"], rec["y"], rec["z"] = pts[:, 0], pts[:, 1], pts[:, 2]
    rec["r"], rec["g"], rec["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {n}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    with path.open("wb") as fh:
        fh.write(header.encode("ascii"))
        fh.write(rec.tobytes())


def write_torus_ply(
    path: Path, major: float = 14.0, minor: float = 5.0, nu: int = 96, nv: int = 48
):
    """A torus above the terrain's centre as a quad mesh with normals and colours."""
    u = np.linspace(0, 2 * np.pi, nu, endpoint=False)
    v = np.linspace(0, 2 * np.pi, nv, endpoint=False)
    uu, vv = np.meshgrid(u, v, indexing="ij")
    cx, cz = np.cos(uu), np.sin(uu)
    x = (major + minor * np.cos(vv)) * cx
    z = (major + minor * np.cos(vv)) * cz
    y = minor * np.sin(vv) + 22.0
    nx, ny, nz = np.cos(vv) * cx, np.sin(vv), np.cos(vv) * cz
    n = nu * nv
    rec = np.empty(
        n,
        dtype=[
            ("x", "<f4"),
            ("y", "<f4"),
            ("z", "<f4"),
            ("nx", "<f4"),
            ("ny", "<f4"),
            ("nz", "<f4"),
            ("red", "u1"),
            ("green", "u1"),
            ("blue", "u1"),
        ],
    )
    rec["x"], rec["y"], rec["z"] = x.ravel(), y.ravel(), z.ravel()
    rec["nx"], rec["ny"], rec["nz"] = nx.ravel(), ny.ravel(), nz.ravel()
    t = uu.ravel() / (2 * np.pi)
    rec["red"] = (40 + 200 * t).astype(np.uint8)
    rec["green"] = (120 + 100 * np.abs(np.sin(vv.ravel()))).astype(np.uint8)
    rec["blue"] = (220 - 180 * t).astype(np.uint8)
    faces = []
    for i in range(nu):
        for j in range(nv):
            a = i * nv + j
            b = ((i + 1) % nu) * nv + j
            c = ((i + 1) % nu) * nv + (j + 1) % nv
            d = i * nv + (j + 1) % nv
            faces.append((4, a, b, c, d))
    frec = np.array(
        faces, dtype=[("n", "u1"), ("a", "<i4"), ("b", "<i4"), ("c", "<i4"), ("d", "<i4")]
    )
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {n}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property float nx\nproperty float ny\nproperty float nz\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        f"element face {len(faces)}\nproperty list uchar int vertex_indices\n"
        "end_header\n"
    )
    with path.open("wb") as fh:
        fh.write(header.encode("ascii"))
        fh.write(rec.tobytes())
        fh.write(frec.tobytes())


def write_splat(path: Path, n: int) -> None:
    pts, rgb = cloud(n)
    pts = pts.copy()
    pts[:, 0] += 230.0  # beside the point terrain so both show
    # .splat files follow the 3DGS/COLMAP frame (y down): viewers, Babylon's SPLAT loader
    # included, negate y on load, so the file holds -y for a y-up scene
    pts[:, 1] *= -1.0
    scale = RNG.uniform(0.15, 0.5, (n, 3)).astype(np.float32)
    q = RNG.normal(0, 1, (n, 4)).astype(np.float32)
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    alpha = RNG.uniform(0.5, 1.0, n).astype(np.float32)
    rec = np.empty(n, dtype=[("p", "<f4", 3), ("s", "<f4", 3), ("c", "u1", 4), ("q", "u1", 4)])
    rec["p"], rec["s"] = pts, scale
    rec["c"][:, :3], rec["c"][:, 3] = rgb, (alpha * 255).astype(np.uint8)
    rec["q"] = np.clip(q * 128 + 128, 0, 255).astype(np.uint8)
    path.write_bytes(rec.tobytes())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--points", type=int, default=1_000_000)
    ap.add_argument("--splats", type=int, default=200_000)
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    scans = root / "scans"
    scans.mkdir(parents=True)
    pts, rgb = cloud(ns.points)
    write_ply_points(scans / "terrain.ply", pts, rgb)
    print(f"[OK] scans/terrain.ply ({ns.points} points)")
    write_torus_ply(scans / "torus.ply")
    print("[OK] scans/torus.ply (quad mesh with normals and colours)")
    write_splat(scans / "cloud.splat", ns.splats)
    print(f"[OK] scans/cloud.splat ({ns.splats} splats)")
    (root / "notes").mkdir()
    (root / "notes" / "readme.md").write_text("# scans review\n", encoding="utf-8")
    print(f"[OK] seeded {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
