"""Scene preparation for the 3D view (spec #35 Implementation Decisions; research #31;
ADR 0016: resolve does the work, the view only reads results).

``prepare_scene(path, cache_dir)`` turns one 3D file into what the web view loads:

- a PLY point cloud becomes a GPU-ready buffer (``float32`` xyz block, then ``uint8``
  rgba block: 16 B/point, what the page hands to ``VertexBuffer`` directly) plus a
  kd-tree for picking (``specto_readers.kdtree``);
- a PLY mesh becomes a GLB (``specto_readers.glb``), so Babylon's glTF loader draws it
  with normals and colours, polygons and big-endian files included;
- glTF/GLB and Gaussian splats (``.ply`` in the INRIA layout, ``.splat``, ``.spz``,
  ``.sog``) pass through to Babylon's own loaders;
- STL and OBJ (SPEC-42) pass through to Babylon's STL and OBJ loaders too (an OBJ's MTL
  and textures load next to it); resolve only measures their bounds and vertex count
  (cached like the PLY results). Neither format carries units or an up axis: the view
  takes the numbers as metres and its ``up`` param says which axis is up;
- a DAE becomes a GLB in metres, Y up (``specto_readers.dae``: next to it as
  ``<name>.dae.glb``, else in the cache), which declares ``up: "y"``.

Results are cached under ``<cache_dir>/scene3d`` keyed by the file's path, size and
mtime; a second resolve of an unchanged file reads the cache. The returned ``scene``
dict is what ends up in ``item.meta["scene"]``::

    {kind: pointcloud|mesh|splat, role: ".ply", files: {points, kdtree} | {glb} | {},
     count?, bounds?: [[min xyz], [max xyz]], up?: "y"}

``up`` is there only for an item whose file declares its up axis (a converted DAE): the
view's ``up`` param turns every other item.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from . import dae
from .glb import write_glb
from .kdtree import KdTree
from .ply import PlyError, classify, read_header, read_ply

MESH_EXTS: tuple[str, ...] = (".glb", ".gltf")
SPLAT_EXTS: tuple[str, ...] = (".splat", ".spz", ".sog")
PLY_EXTS: tuple[str, ...] = (".ply",)
#: meshes Babylon loads itself, whose bounds resolve measures
LOADER_EXTS: tuple[str, ...] = (".stl", ".obj")
DAE_EXTS: tuple[str, ...] = (".dae",)
SCENE_EXTS: tuple[str, ...] = PLY_EXTS + MESH_EXTS + SPLAT_EXTS + LOADER_EXTS + DAE_EXTS
CACHE_SUBDIR = "scene3d"

Progress = Callable[[float, str], None] | None


def scene_role(parts: dict[str, str]) -> str | None:
    """The part of an item the 3D view shows (the first 3D extension), or None."""
    for role in parts:
        if role.lower() in SCENE_EXTS:
            return role
    return None


def cache_key(path: Path) -> str:
    st = path.stat()
    text = f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha1(text.encode("utf-8", errors="surrogateescape")).hexdigest()[:20]


def prepare_scene(path: Path | str, cache_dir: Path | str) -> dict[str, Any]:
    """The scene entry for one file (see the module docstring); raises ``PlyError`` or
    ``OSError`` when the file cannot be read."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in MESH_EXTS:
        return {"kind": "mesh", "role": ext, "files": {}}
    if ext in SPLAT_EXTS:
        return {"kind": "splat", "role": ext, "files": {}}
    if ext in DAE_EXTS:
        return dae.prepare(path, cache_dir, cache_key(path))
    if ext in LOADER_EXTS:
        return _cached(path, cache_dir, lambda: _measured(path, ext))
    if ext not in PLY_EXTS:
        raise PlyError(f"{path}: not a 3D file the scene knows ({', '.join(SCENE_EXTS)})")
    header = read_header(path)
    kind = classify(header)
    if kind == "splat":
        return {"kind": "splat", "role": ext, "files": {}}
    cache = Path(cache_dir) / CACHE_SUBDIR
    key = cache_key(path)
    meta_path = cache / f"{key}.json"
    if meta_path.is_file():
        try:
            cached = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            cached = None
        if cached and all(Path(p).is_file() for p in cached.get("files", {}).values()):
            return cached
    cache.mkdir(parents=True, exist_ok=True)
    data = read_ply(path, header)
    positions = data.positions()
    if kind == "mesh":
        glb = cache / f"{key}.glb"
        write_glb(
            glb,
            positions,
            indices=data.faces,
            normals=data.normals(),
            colors=data.colors(),
            name=path.stem,
        )
        scene = {
            "kind": "mesh",
            "role": ext,
            "files": {"glb": str(glb)},
            "count": len(positions),
            "bounds": _bounds(positions),
        }
    else:
        colors = data.colors()
        if colors is None:
            colors = np.full((len(positions), 4), 255, dtype=np.uint8)
        points = cache / f"{key}.points.bin"
        tmp = points.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            fh.write(np.ascontiguousarray(positions, dtype="<f4").tobytes())
            fh.write(np.ascontiguousarray(colors, dtype=np.uint8).tobytes())
        os.replace(tmp, points)
        kd = cache / f"{key}.kd"
        KdTree.build(positions).save(kd)
        scene = {
            "kind": "pointcloud",
            "role": ext,
            "files": {"points": str(points), "kdtree": str(kd)},
            "count": len(positions),
            "bounds": _bounds(positions),
        }
    meta_path.write_text(json.dumps(scene), encoding="utf-8")
    return scene


def _cached(
    path: Path, cache_dir: Path | str, make: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """A scene entry that holds no derived files, cached as JSON by the file's identity."""
    cache = Path(cache_dir) / CACHE_SUBDIR
    meta_path = cache / f"{cache_key(path)}.json"
    if meta_path.is_file():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    scene = make()
    cache.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(scene), encoding="utf-8")
    return scene


_STL_VERTEX = re.compile(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)")
_OBJ_VERTEX = re.compile(rb"^[ 	]*v[ 	]+(\S+)[ 	]+(\S+)[ 	]+(\S+)", re.MULTILINE)
_STL_FACET = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])


def stl_positions(data: bytes) -> np.ndarray:
    """An STL's vertex positions, (3 x triangles, 3) float32: binary when its size is the
    binary layout's (84 + 50 per triangle) or it does not start with ``solid``, else
    ASCII (the test Babylon's loader makes)."""
    n = struct.unpack_from("<I", data, 80)[0] if len(data) >= 84 else -1
    binary = len(data) >= 84 and (84 + 50 * n == len(data) or not data.startswith(b"solid"))
    if binary:
        n = min(n, (len(data) - 84) // 50)
        facets = np.frombuffer(data, dtype=_STL_FACET, count=n, offset=84)
        return facets["v"].reshape(-1, 3).astype(np.float32)
    return _matched(_STL_VERTEX, data)


def obj_positions(data: bytes) -> np.ndarray:
    """An OBJ's ``v`` lines as (n, 3) float32 (colours after xyz are ignored)."""
    return _matched(_OBJ_VERTEX, data)


def _matched(pattern: re.Pattern[bytes], data: bytes) -> np.ndarray:
    rows = pattern.findall(data)
    if not rows:
        return np.zeros((0, 3), dtype=np.float32)
    try:
        return np.array(rows, dtype=np.float64).astype(np.float32)
    except ValueError as e:
        raise PlyError(f"unreadable vertex: {e}") from e


def _measured(path: Path, ext: str) -> dict[str, Any]:
    data = path.read_bytes()
    positions = stl_positions(data) if ext == ".stl" else obj_positions(data)
    scene: dict[str, Any] = {"kind": "mesh", "role": ext, "files": {}, "count": len(positions)}
    if len(positions):
        scene["bounds"] = _bounds(positions)
    return scene


def _bounds(positions: np.ndarray) -> list[list[float]]:
    if len(positions) == 0:
        return [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    return [
        [float(x) for x in positions.min(axis=0)],
        [float(x) for x in positions.max(axis=0)],
    ]


def load_points(points_file: Path | str, count: int) -> tuple[np.ndarray, np.ndarray]:
    """The decoded buffer back as arrays: ``(xyz float32 (n, 3), rgba uint8 (n, 4))``,
    memory-mapped (the 20 M-point case is 320 MB)."""
    mm = np.memmap(points_file, mode="r", dtype=np.uint8)
    xyz = np.frombuffer(mm, dtype="<f4", count=count * 3, offset=0).reshape(count, 3)
    rgba = np.frombuffer(mm, dtype=np.uint8, count=count * 4, offset=count * 12).reshape(count, 4)
    return xyz, rgba


__all__ = [
    "CACHE_SUBDIR",
    "DAE_EXTS",
    "LOADER_EXTS",
    "MESH_EXTS",
    "PLY_EXTS",
    "SCENE_EXTS",
    "SPLAT_EXTS",
    "cache_key",
    "load_points",
    "obj_positions",
    "prepare_scene",
    "scene_role",
    "stl_positions",
]
