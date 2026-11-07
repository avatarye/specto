"""COLLADA (``.dae``) for the 3D view (SPEC-42, ADR 0028): Babylon has no COLLADA loader,
so resolve converts a DAE to a GLB that Babylon's glTF loader draws.

- **Converter**: trimesh (MIT) reading through pycollada (BSD-3-Clause), both pure Python
  on numpy; textures are decoded with Pillow and embedded in the GLB. trimesh keeps each
  instance's accumulated node transform (the hierarchy itself is flattened under the
  root), triangles and polylists, normals, UVs, vertex colours and the common profile's
  materials (diffuse colour or texture, emission, transparency, shininess as roughness).
  Cameras, lights, animation and skins are dropped.
- **Units and up axis**: neither trimesh nor pycollada applies them, so ``convert`` does:
  ``<unit meter>`` scales to metres and ``<up_axis>`` turns the scene to glTF's Y up with
  a rotation (never a mirror): Z_UP maps (x, y, z) to (x, z, -y), X_UP to (-y, x, z),
  as COLLADA's axis table says (right, up, in). The result is metres, Y up, so the scene
  entry declares ``up: "y"`` and the view's ``up`` param leaves it alone.
- **Where the GLB goes** (``prepare``): next to the DAE as ``<name>.dae.glb`` (the
  converted mesh), regenerated when the DAE is newer than it; when the DAE's folder cannot
  take it (read-only, a stale read-only converted mesh), into the cache folder,
  ``<cache>/scene3d/<file key>.dae.glb``, keyed by the DAE's path, size and mtime.
"""

from __future__ import annotations

import json
import os
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

CACHE_SUBDIR = "scene3d"
CONVERTED_SUFFIX = ".glb"
#: what a converted mesh's name ends with (case-insensitively)
CONVERTED_TAIL = ".dae" + CONVERTED_SUFFIX
UP_AXES = ("X_UP", "Y_UP", "Z_UP")


class DaeError(ValueError):
    """A DAE that cannot be converted (unreadable, malformed, nothing to draw)."""


@dataclass(frozen=True)
class DaeAsset:
    """The ``<asset>`` facts the conversion applies: metres per unit and the up axis
    (COLLADA's defaults: 1.0 and Y_UP)."""

    unit_meter: float = 1.0
    up_axis: str = "Y_UP"


def converted_path(dae_path: Path | str) -> Path:
    """``robot.dae`` -> ``robot.dae.glb`` beside it."""
    p = Path(dae_path)
    return p.with_name(p.name + CONVERTED_SUFFIX)


def is_converted(path: Path | str) -> bool:
    """Whether a file's name is a converted DAE's (``*.dae.glb``)."""
    return Path(path).name.lower().endswith(CONVERTED_TAIL)


def read_asset(path: Path | str) -> DaeAsset:
    """The DAE's ``<asset>`` unit and up axis, read without parsing the rest of the file."""
    unit, up = 1.0, "Y_UP"
    try:
        for _event, el in ET.iterparse(str(path), events=("end",)):
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "unit":
                try:
                    unit = float(el.get("meter", "1")) or 1.0
                except ValueError:
                    unit = 1.0
            elif tag == "up_axis":
                text = (el.text or "").strip().upper()
                up = text if text in UP_AXES else "Y_UP"
            elif tag == "asset":
                break
    except ET.ParseError as e:
        raise DaeError(f"{Path(path).name}: not a readable COLLADA file ({e})") from e
    return DaeAsset(unit_meter=unit, up_axis=up)


def to_gltf_frame(asset: DaeAsset) -> np.ndarray:
    """The 4x4 that takes the DAE's coordinates to glTF's: metres, Y up, a rotation."""
    rot = {
        "Y_UP": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "Z_UP": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
        "X_UP": [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
    }[asset.up_axis]
    m = np.eye(4)
    m[:3, :3] = np.array(rot, dtype=float) * asset.unit_meter
    return m


def convert(src: Path | str, dst: Path | str) -> dict[str, Any]:
    """Convert one DAE to a GLB at ``dst`` (written whole or not at all); returns
    ``{count, bounds}``: vertices written and the scene's box in metres, Y up. Raises
    ``DaeError`` when there is nothing to convert; ``OSError`` when ``dst`` cannot be
    written."""
    src, dst = Path(src), Path(dst)
    data, info = _convert_bytes(src)
    _write_atomic(data, dst)
    return info


def _convert_bytes(src: Path) -> tuple[bytes, dict[str, Any]]:
    import trimesh  # heavy import, paid only when a DAE is converted

    asset = read_asset(src)
    try:
        # process=False: vertices stay as the file has them (no merging across UV seams)
        scene = trimesh.load_scene(str(src), file_type="dae", process=False)
    except Exception as e:  # pycollada and trimesh raise many kinds
        raise DaeError(f"{src.name}: {type(e).__name__}: {e}") from e
    if not scene.geometry:
        raise DaeError(f"{src.name}: no geometry to show")
    scene.apply_transform(to_gltf_frame(asset))
    count = 0
    for node in scene.graph.nodes_geometry:
        _transform, geom_name = scene.graph[node]
        geom = scene.geometry.get(geom_name)
        count += len(getattr(geom, "vertices", ()))
    bounds = scene.bounds
    data = scene.export(file_type="glb")
    return data, {"count": int(count), "bounds": _bounds_list(bounds)}


def _bounds_list(bounds: Any) -> list[list[float]] | None:
    if bounds is None:
        return None
    return [[round(float(x), 9) for x in row] for row in np.asarray(bounds)]


def _write_atomic(data: bytes, dst: Path) -> None:
    """``dst`` gets ``data`` whole or not at all (a temporary beside it, then a rename)."""
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dst)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _fresh(glb: Path, src: Path) -> bool:
    try:
        return glb.is_file() and glb.stat().st_mtime_ns >= src.stat().st_mtime_ns
    except OSError:
        return False


def _glb_bounds(glb: Path) -> dict[str, Any]:
    """``{count, bounds}`` of an existing GLB (a converted mesh this cache has not seen)."""
    import trimesh

    try:
        scene = trimesh.load_scene(str(glb), file_type="glb", process=False)
    except Exception as e:
        raise DaeError(f"{glb.name}: {type(e).__name__}: {e}") from e
    count = sum(len(getattr(g, "vertices", ())) for g in scene.geometry.values())
    return {"count": int(count), "bounds": _bounds_list(scene.bounds)}


def prepare(src: Path | str, cache_dir: Path | str, key: str) -> dict[str, Any]:
    """The 3D view's scene entry for a DAE: ``{kind: mesh, role: .dae, files: {glb},
    count, bounds, up: "y"}``, converting when no fresh GLB exists (module docstring).
    ``key`` names the DAE's identity (path, size, mtime) in the cache."""
    src = Path(src)
    cache = Path(cache_dir) / CACHE_SUBDIR
    meta_path = cache / f"{key}.dae.json"
    if meta_path.is_file():
        try:
            cached = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            cached = None
        if cached and _fresh(Path(cached.get("files", {}).get("glb", "")), src):
            return cached
    side = converted_path(src)
    if _fresh(side, src):
        glb, info = side, _glb_bounds(side)
    else:
        data, info = _convert_bytes(src)
        glb = side
        try:
            _write_atomic(data, side)
        except OSError:  # the DAE's folder cannot take it: the cache can
            glb = cache / f"{key}{CONVERTED_TAIL}"
            cache.mkdir(parents=True, exist_ok=True)
            _write_atomic(data, glb)
    scene: dict[str, Any] = {"kind": "mesh", "role": ".dae", "files": {"glb": str(glb)}}
    scene["count"] = info["count"]
    if info.get("bounds") is not None:
        scene["bounds"] = info["bounds"]
    scene["up"] = "y"
    cache.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(scene), encoding="utf-8")
    return scene


__all__ = [
    "CACHE_SUBDIR",
    "CONVERTED_SUFFIX",
    "CONVERTED_TAIL",
    "DaeAsset",
    "DaeError",
    "convert",
    "converted_path",
    "is_converted",
    "prepare",
    "read_asset",
    "to_gltf_frame",
]
