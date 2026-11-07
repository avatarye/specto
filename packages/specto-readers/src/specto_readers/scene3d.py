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
  (cached like the PLY results; files over ``LOADER_MAX_BYTES`` are refused). Neither
  format carries units or an up axis: the view's ``mesh_unit`` param says what a unit is
  and its ``up`` param which axis is up. An OBJ's MTL and textures that do not resolve
  inside ``root`` are listed under ``missing``;
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
from .scenecache import CACHE_SUBDIR, cache_folder, cached, read_record, write_record

MESH_EXTS: tuple[str, ...] = (".glb", ".gltf")
SPLAT_EXTS: tuple[str, ...] = (".splat", ".spz", ".sog")
PLY_EXTS: tuple[str, ...] = (".ply",)
#: meshes Babylon loads itself, whose bounds resolve measures
LOADER_EXTS: tuple[str, ...] = (".stl", ".obj")
DAE_EXTS: tuple[str, ...] = (".dae",)
SCENE_EXTS: tuple[str, ...] = PLY_EXTS + MESH_EXTS + SPLAT_EXTS + LOADER_EXTS + DAE_EXTS

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


def prepare_scene(
    path: Path | str, cache_dir: Path | str, *, root: Path | str | None = None
) -> dict[str, Any]:
    """The scene entry for one file (see the module docstring); raises ``PlyError``,
    ``MeshError``, ``DaeError`` or ``OSError`` when the file cannot be shown. ``root`` (the
    project or source root) bounds the files an OBJ may name: one outside it, or missing,
    is listed under ``missing``."""
    root = Path(root) if root is not None else None
    path = Path(path)
    ext = path.suffix.lower()
    if ext in MESH_EXTS:
        return {"kind": "mesh", "role": ext, "files": {}}
    if ext in SPLAT_EXTS:
        return {"kind": "splat", "role": ext, "files": {}}
    if ext in DAE_EXTS:
        return dae.prepare(path, cache_dir, cache_key(path))
    if ext in LOADER_EXTS:
        scene = dict(_measured(path, ext, cache_dir))
        libs = scene.pop("mtllib", None)
        if libs:  # checked on every resolve: a texture may come or go
            missing = unreachable(path, obj_references(path, libs), root)
            if missing:
                scene["missing"] = missing
        return scene
    if ext not in PLY_EXTS:
        raise PlyError(f"{path}: not a 3D file the scene knows ({', '.join(SCENE_EXTS)})")
    header = read_header(path)
    kind = classify(header)
    if kind == "splat":
        return {"kind": "splat", "role": ext, "files": {}}
    cache = cache_folder(cache_dir)
    key = cache_key(path)
    meta_path = cache / f"{key}.json"
    rec = read_record(meta_path)
    if rec and all(Path(p).is_file() for p in rec.get("files", {}).values()):
        return rec
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
    write_record(meta_path, scene)
    return scene


#: STL and OBJ files over this are refused at resolve (Babylon parses them whole in the
#: page; the resolve-time scan below reads them in blocks)
LOADER_MAX_BYTES = 512 * 1024 * 1024
SCAN_CHUNK = 16 * 1024 * 1024
_STL_VERTEX = re.compile(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)")
_OBJ_VERTEX = re.compile(rb"^[ \t]*v[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)", re.MULTILINE)
_OBJ_MTLLIB = re.compile(rb"^[ \t]*mtllib[ \t]+([^\r\n]+?)[ \t]*\r?$", re.MULTILINE)
_MTL_MAP = re.compile(r"^\s*(map_ka|map_kd|map_ks|map_d|map_bump)\s+(.+?)\s*$", re.I | re.M)
_STL_FACET = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])


class MeshError(ValueError):
    """An STL or OBJ the scene does not take (too big, an unreadable vertex)."""


class _Extent:
    """Vertex count and box, grown block by block."""

    def __init__(self) -> None:
        self.count = 0
        self.lo = np.full(3, np.inf)
        self.hi = np.full(3, -np.inf)

    def add(self, xyz: np.ndarray) -> None:
        if len(xyz):
            self.count += len(xyz)
            self.lo = np.minimum(self.lo, xyz.min(axis=0))
            self.hi = np.maximum(self.hi, xyz.max(axis=0))

    def entry(self, ext: str) -> dict[str, Any]:
        scene: dict[str, Any] = {"kind": "mesh", "role": ext, "files": {}, "count": self.count}
        if self.count:
            scene["bounds"] = [[float(x) for x in self.lo], [float(x) for x in self.hi]]
        return scene


def _rows(pattern: re.Pattern[bytes], block: bytes) -> np.ndarray:
    rows = pattern.findall(block)
    if not rows:
        return np.zeros((0, 3), dtype=np.float32)
    try:
        return np.array(rows, dtype=np.float64).astype(np.float32)
    except ValueError as e:
        raise MeshError(f"unreadable vertex: {e}") from e


def _text_blocks(path: Path):
    """The file in blocks of about ``SCAN_CHUNK`` that end on a line break."""
    carry = b""
    with path.open("rb") as fh:
        while True:
            block = fh.read(SCAN_CHUNK)
            if not block:
                if carry:
                    yield carry
                return
            data = carry + block
            cut = data.rfind(b"\n")
            if cut < 0:
                carry = data
                continue
            carry = data[cut + 1 :]
            yield data[: cut + 1]


def _stl_is_binary(path: Path, size: int) -> bool:
    """Binary when the size is the binary layout's (84 + 50 per triangle) or the file does
    not start with ``solid`` (the test Babylon's loader makes)."""
    if size < 84:
        return False
    with path.open("rb") as fh:
        head = fh.read(84)
    n = struct.unpack_from("<I", head, 80)[0]
    return 84 + 50 * n == size or not head.startswith(b"solid")


def measure_stl(path: Path) -> dict[str, Any]:
    """An STL's vertex count (3 per triangle) and box, read in blocks."""
    size = path.stat().st_size
    ext = _Extent()
    if _stl_is_binary(path, size):
        n = (size - 84) // 50
        if n:
            facets = np.memmap(path, dtype=_STL_FACET, mode="r", offset=84, shape=(n,))
            step = max(1, SCAN_CHUNK // 50)
            for i in range(0, n, step):
                ext.add(np.asarray(facets["v"][i : i + step]).reshape(-1, 3))
            del facets
    else:
        for block in _text_blocks(path):
            ext.add(_rows(_STL_VERTEX, block))
    return ext.entry(".stl")


def measure_obj(path: Path) -> dict[str, Any]:
    """An OBJ's ``v`` lines (colours after xyz ignored) and its ``mtllib`` names."""
    ext = _Extent()
    libs: list[str] = []
    for block in _text_blocks(path):
        ext.add(_rows(_OBJ_VERTEX, block))
        libs += [m.decode("utf-8", "replace") for m in _OBJ_MTLLIB.findall(block)]
    scene = ext.entry(".obj")
    if libs:
        scene["mtllib"] = libs
    return scene


def obj_references(obj: Path, mtllibs: list[str]) -> list[str]:
    """The files an OBJ names as Babylon's loader reads them, all relative to the OBJ's
    folder: each ``mtllib`` (the rest of its line), then the texture maps in each MTL
    found (``map_Kd`` and kin, ``-bm <n>`` dropped)."""
    refs: list[str] = []
    for lib in mtllibs:
        refs.append(lib)
        mtl = obj.parent / lib
        try:
            text = mtl.read_text(encoding="utf-8", errors="replace") if mtl.is_file() else ""
        except OSError:
            text = ""
        for m in _MTL_MAP.finditer(text):
            words = m.group(2).split()
            if m.group(1).lower() == "map_bump" and "-bm" in words:
                i = words.index("-bm")
                del words[i : i + 2]
            if words:
                refs.append(" ".join(words))
    return refs


def unreachable(obj: Path, refs: list[str], root: Path | None) -> list[str]:
    """The references that do not resolve to a file, or that resolve outside ``root``
    (the project or source root; None: anywhere)."""
    out: list[str] = []
    base = obj.parent
    top = root.resolve() if root is not None else None
    for ref in refs:
        rel = ref.replace("\\", "/")
        if Path(rel).is_absolute() or ":" in rel:
            out.append(ref)
            continue
        try:
            target = (base / rel).resolve()
        except (OSError, ValueError):
            out.append(ref)
            continue
        inside = top is None or target.is_relative_to(top)
        if not inside or not target.is_file():
            out.append(ref)
    return out


def _measured(path: Path, ext: str, cache_dir: Path | str) -> dict[str, Any]:
    size = path.stat().st_size
    if size > LOADER_MAX_BYTES:
        raise MeshError(
            f"{path.name}: {size / 2**20:.0f} MB; the 3d view opens {ext[1:].upper()} files "
            f"up to {LOADER_MAX_BYTES / 2**20:.0f} MB"
        )
    measure = measure_stl if ext == ".stl" else measure_obj
    return cached(cache_folder(cache_dir) / f"{cache_key(path)}.json", lambda: measure(path))


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
    "LOADER_MAX_BYTES",
    "MESH_EXTS",
    "PLY_EXTS",
    "SCENE_EXTS",
    "SPLAT_EXTS",
    "MeshError",
    "cache_key",
    "load_points",
    "measure_obj",
    "measure_stl",
    "obj_references",
    "prepare_scene",
    "scene_role",
    "unreachable",
]
