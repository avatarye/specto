"""Seam 1 (#47): a 3D view fed by an intaker. When the intaker's target is a ``3d`` view,
resolve prepares the scene: point-cloud PLYs are decoded to GPU-ready buffers (float32
xyz block, uint8 rgba block) with a kd-tree for picking, PLY meshes become GLB, and
glTF/GLB and splat files pass through. The collection delivered on the view's ``data``
port names all of it under ``item.meta["scene"]``. Expected bytes are written out by
hand, not recomputed."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import Collection
from specto_readers.kdtree import KdTree

# five points: xyz and rgb, written as a binary little-endian PLY (x y z float, rgb uchar)
POINTS = [
    (-1.0, 0.0, 1.0),
    (0.0, 1.0, -2.0),
    (1.5, 2.0, 0.0),
    (3.0, 4.0, 2.5),
    (2.0, 0.5, 1.0),
]
COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (10, 20, 30), (200, 200, 200)]


def write_cloud_ply(path: Path) -> None:
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(POINTS)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    body = b"".join(struct.pack("<fffBBB", *p, *c) for p, c in zip(POINTS, COLORS, strict=True))
    path.write_bytes(header.encode("ascii") + body)


def write_quad_ply(path: Path) -> None:
    """An ASCII PLY mesh: four vertices, one triangle and one quad (fanned to two)."""
    path.write_text(
        "ply\nformat ascii 1.0\n"
        "element vertex 4\nproperty float x\nproperty float y\nproperty float z\n"
        "element face 2\nproperty list uchar int vertex_indices\nend_header\n"
        "0 0 0\n1 0 0\n1 1 0\n0 1 0\n"
        "3 0 1 2\n4 0 1 2 3\n",
        encoding="ascii",
    )


def write_splat_ply(path: Path) -> None:
    """One Gaussian splat in the INRIA layout: left for Babylon's SPLAT loader."""
    names = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity"]
    names += ["scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    header = "ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
    header += "".join(f"property float {n}\n" for n in names) + "end_header\n"
    path.write_bytes(header.encode("ascii") + struct.pack("<17f", *([0.5] * 17)))


def seed(root: Path) -> Path:
    scans = root / "scans"
    scans.mkdir(parents=True)
    write_cloud_ply(scans / "cloud.ply")
    write_quad_ply(scans / "quad.ply")
    write_splat_ply(scans / "gauss.ply")
    (scans / "raw.splat").write_bytes(bytes(64))
    (scans / "model.glb").write_bytes(b"glTF" + bytes(8))
    (scans / "notes.txt").write_text("not 3d\n", encoding="ascii")
    return scans


def scene_document(doc: dict) -> dict:
    doc["views"]["kobi"] = {
        "type": "3d",
        "label": "Scans",
        "layout": {"position": [300, 0], "size": [800, 600]},
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "label": "scans",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["scans"], "screener": ["@3d"]},
        "summary": {"sentence": "The 3D view shows every scan under scans/."},
    }
    return doc


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    proj = Project.init(tmp_path / "proj", "scans")
    seed(proj.root)
    return Engine(project=proj, jobs=InlineJobManager())


def scenes(col: Collection) -> dict[str, dict]:
    return {it.key: it.meta["scene"] for it in col.items}


def test_submitting_a_3d_view_resolves_its_scene_buffers(engine: Engine):
    engine.live.submit(scene_document(engine.live.document()), origin="agent:test", label="3d")

    col = engine.links.input_of("kobi", "data")
    assert isinstance(col, Collection)
    by = scenes(col)
    # the screener alias keeps the 3D files and nothing else
    assert sorted(by) == ["cloud", "gauss", "model", "quad", "raw"]

    cloud = by["cloud"]
    assert cloud["kind"] == "pointcloud" and cloud["role"] == ".ply" and cloud["count"] == 5
    assert cloud["bounds"] == [[-1.0, 0.0, -2.0], [3.0, 4.0, 2.5]]
    # the GPU buffer: float32 xyz for every point, then uint8 rgba for every point
    xyz = b"".join(struct.pack("<fff", *p) for p in POINTS)
    rgba = b"".join(bytes([*c, 255]) for c in COLORS)
    assert Path(cloud["files"]["points"]).read_bytes() == xyz + rgba
    # the kd-tree built at resolve answers picks against the same points
    tree = KdTree.load(cloud["files"]["kdtree"], np.array(POINTS, dtype=np.float32))
    assert tree.nearest([2.9, 3.9, 2.4]) == (3, pytest.approx(0.17320508))
    assert tree.nearest([0.0, 0.0, 0.0]) == (0, pytest.approx(1.41421356))

    quad = by["quad"]
    assert quad["kind"] == "mesh" and quad["role"] == ".ply"
    glb = Path(quad["files"]["glb"]).read_bytes()
    magic, version, length = struct.unpack_from("<4sII", glb, 0)
    assert magic == b"glTF" and version == 2 and length == len(glb)
    chunk_len, chunk_type = struct.unpack_from("<I4s", glb, 12)
    assert chunk_type == b"JSON"
    gltf = json.loads(glb[20 : 20 + chunk_len])
    prim = gltf["meshes"][0]["primitives"][0]
    assert gltf["accessors"][prim["attributes"]["POSITION"]]["count"] == 4
    assert gltf["accessors"][prim["indices"]]["count"] == 9  # 3 + a quad fanned into 6
    assert gltf["accessors"][prim["attributes"]["POSITION"]]["min"] == [0.0, 0.0, 0.0]
    assert gltf["accessors"][prim["attributes"]["POSITION"]]["max"] == [1.0, 1.0, 0.0]

    # Babylon's own loaders take these as they are
    assert by["gauss"] == {"kind": "splat", "role": ".ply", "files": {}}
    assert by["raw"] == {"kind": "splat", "role": ".splat", "files": {}}
    assert by["model"] == {"kind": "mesh", "role": ".glb", "files": {}}


def test_the_file_tree_intaker_does_not_decode_3d_files(engine: Engine, tmp_path: Path):
    col = engine.links.input_of("fege", "data")
    assert isinstance(col, Collection) and not any("scene" in it.meta for it in col.items)
    assert not (tmp_path / "cache" / "scene3d").exists()


def test_scene_buffers_are_cached_by_file_identity(engine: Engine, tmp_path: Path):
    engine.live.submit(scene_document(engine.live.document()), origin="agent:test", label="3d")
    first = scenes(engine.links.input_of("kobi", "data"))["cloud"]["files"]
    stamp = Path(first["points"]).stat().st_mtime_ns
    # the same file resolves to the same buffers without decoding again
    engine.links.resolve("l2")
    again = scenes(engine.links.input_of("kobi", "data"))["cloud"]["files"]
    assert again == first and Path(again["points"]).stat().st_mtime_ns == stamp
    # a changed file gets new buffers
    write_quad_ply(tmp_path / "proj" / "scans" / "cloud.ply")
    engine.links.resolve("l2")
    changed = scenes(engine.links.input_of("kobi", "data"))["cloud"]
    assert changed["kind"] == "mesh" and changed["files"]["glb"] != first["points"]
