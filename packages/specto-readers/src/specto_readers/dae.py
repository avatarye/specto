"""COLLADA (``.dae``) for the 3D view (SPEC-42, ADR 0028): Babylon has no COLLADA loader,
so resolve converts a DAE to a GLB that Babylon's glTF loader draws.

- **Converter**: trimesh (MIT) reading through pycollada (BSD-3-Clause), both pure Python
  on numpy; textures are decoded with Pillow and embedded in the GLB. pycollada parses
  with the standard library's ElementTree (lxml is not installed; it would also resolve
  external entities). trimesh keeps each instance's accumulated node transform (the
  hierarchy itself is flattened under the root), triangles and polylists, normals, UVs,
  vertex colours and the common profile's materials (diffuse colour or texture, emission,
  transparency, shininess as roughness). Cameras, lights, animation and skins are
  dropped.
- **Units and up axis**: neither trimesh nor pycollada applies them, so the conversion
  does: ``<unit meter>`` scales to metres and ``<up_axis>`` turns the scene to glTF's Y up
  with a rotation (never a mirror): Z_UP maps (x, y, z) to (x, z, -y), X_UP to
  (-y, x, z), as COLLADA's axis table says (right, up, in). The result is metres, Y up,
  so the scene entry declares ``up: "y"`` and the view's ``up`` param leaves it alone.
- **Bounded**: a DAE over ``DAE_MAX_BYTES`` is refused, and the conversion runs in a child
  Python (``python -m specto_readers.dae``) that is killed after ``CONVERT_TIMEOUT_S``.
- **Where the GLB goes** (``prepare``): next to the DAE as ``<name>.dae.glb`` (the
  converted mesh). The GLB carries the DAE's size and mtime it was made from (and the
  converter's version) in ``asset.extras.specto``, and is used only while they match, so
  a DAE swapped for an older copy is converted again too. When the DAE's folder cannot
  take the file (read-only, a read-only stale converted mesh in the way, a path too long,
  another writer holding it), it goes to the cache folder instead,
  ``<cache>/scene3d/<file key>.dae.glb``.
- **Version**: the scene entry's ``version`` names the source state, so a view that has
  the model open loads the new GLB when the DAE changes (its URL changes).
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .scenecache import cache_folder, cached, write_atomic

CONVERTED_SUFFIX = ".glb"
#: what a converted mesh's name ends with (case-insensitively)
CONVERTED_TAIL = ".dae" + CONVERTED_SUFFIX
UP_AXES = ("X_UP", "Y_UP", "Z_UP")
#: bumped when the conversion changes what it writes: older converted meshes are redone
CONVERTER_VERSION = 1
DAE_MAX_BYTES = 256 * 1024 * 1024
CONVERT_TIMEOUT_S = 120.0


class DaeError(ValueError):
    """A DAE that cannot be converted (unreadable, malformed, too big, too slow, nothing
    to draw)."""


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


def source_stamp(src: Path) -> dict[str, int]:
    """What a converted mesh was made from: the DAE's size and mtime, and the converter."""
    st = src.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "converter": CONVERTER_VERSION}


def version_of(stamp: dict[str, int]) -> str:
    return f"{stamp['size']}-{stamp['mtime_ns']}-{stamp['converter']}"


# -- the GLB's own record --------------------------------------------------------------------


def _split_glb(data: bytes) -> tuple[dict[str, Any], bytes]:
    magic, version, _length = struct.unpack_from("<4sII", data, 0)
    n, kind = struct.unpack_from("<I4s", data, 12)
    if magic != b"glTF" or version != 2 or kind != b"JSON":
        raise DaeError("not a GLB")
    return json.loads(data[20 : 20 + n]), data[20 + n :]


def _join_glb(gltf: dict[str, Any], rest: bytes) -> bytes:
    text = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    head = struct.pack("<I4s", len(text), b"JSON") + text
    return struct.pack("<4sII", b"glTF", 2, 12 + len(head) + len(rest)) + head + rest


def with_record(data: bytes, record: dict[str, Any]) -> bytes:
    """The GLB with ``asset.extras.specto`` set to ``record``."""
    gltf, rest = _split_glb(data)
    asset = gltf.setdefault("asset", {"version": "2.0"})
    asset.setdefault("extras", {})["specto"] = record
    return _join_glb(gltf, rest)


def glb_record(path: Path) -> dict[str, Any] | None:
    """A converted mesh's ``asset.extras.specto`` (its source stamp, count and bounds), or
    None when the file is missing, not a GLB, or not one Specto wrote."""
    try:
        with path.open("rb") as fh:
            head = fh.read(20)
            if len(head) < 20:
                return None
            n, kind = struct.unpack_from("<I4s", head, 12)
            if head[:4] != b"glTF" or kind != b"JSON" or n > 64 * 1024 * 1024:
                return None
            gltf = json.loads(fh.read(n))
    except (OSError, ValueError, struct.error):
        return None
    rec = ((gltf.get("asset") or {}).get("extras") or {}).get("specto")
    return rec if isinstance(rec, dict) else None


def _matches(glb: Path, stamp: dict[str, int]) -> dict[str, Any] | None:
    rec = glb_record(glb)
    return rec if rec is not None and rec.get("source") == stamp else None


# -- the conversion --------------------------------------------------------------------------


def scene_info(scene: Any) -> dict[str, Any]:
    """``{count, bounds}`` of a trimesh scene: vertices of every placed instance, and the
    world box (None when empty)."""
    count = 0
    for node in scene.graph.nodes_geometry:
        _transform, geom_name = scene.graph[node]
        count += len(getattr(scene.geometry.get(geom_name), "vertices", ()))
    bounds = scene.bounds
    if bounds is None:
        return {"count": int(count), "bounds": None}
    return {
        "count": int(count),
        "bounds": [[round(float(x), 9) for x in row] for row in np.asarray(bounds)],
    }


def _convert_here(src: Path, stamp: dict[str, int]) -> bytes:
    """The conversion itself (in the child process): the GLB bytes, its record set."""
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
    info = scene_info(scene)
    return with_record(scene.export(file_type="glb"), {"source": stamp, **info})


def _child_main(argv: list[str]) -> int:
    src, out = Path(argv[0]), Path(argv[1])
    try:
        data = _convert_here(src, json.loads(argv[2]))
    except DaeError as e:
        print(json.dumps({"error": str(e)}))
        return 2
    write_atomic(data, out)
    print(json.dumps({"ok": True}))
    return 0


def _convert_bounded(src: Path, out: Path, stamp: dict[str, int]) -> None:
    """Convert in a child Python killed after ``CONVERT_TIMEOUT_S``; the GLB lands at
    ``out``. Raises ``DaeError``."""
    size = stamp["size"]
    if size > DAE_MAX_BYTES:
        raise DaeError(
            f"{src.name}: {size / 2**20:.0f} MB; the 3d view converts DAE files up to "
            f"{DAE_MAX_BYTES / 2**20:.0f} MB"
        )
    cmd = [sys.executable, "-m", "specto_readers.dae", str(src), str(out), json.dumps(stamp)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CONVERT_TIMEOUT_S,
            check=False,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired as e:
        out.unlink(missing_ok=True)
        raise DaeError(
            f"{src.name}: conversion took longer than {CONVERT_TIMEOUT_S:g} s, stopped"
        ) from e
    said = (res.stdout or "").strip().splitlines()
    answer: dict[str, Any] = {}
    if said:
        try:
            answer = json.loads(said[-1])
        except ValueError:
            answer = {}
    if res.returncode != 0 or not answer.get("ok"):
        if answer.get("error"):
            raise DaeError(str(answer["error"]))
        tail = (res.stderr or "").strip().splitlines()
        raise DaeError(f"{src.name}: conversion failed: {tail[-1] if tail else res.returncode}")


def convert(src: Path | str, dst: Path | str) -> dict[str, Any]:
    """Convert one DAE to a GLB at ``dst`` (bounded; written whole or not at all); returns
    the GLB's record: ``{source, count, bounds}``."""
    src, dst = Path(src), Path(dst)
    _convert_bounded(src, dst, source_stamp(src))
    rec = glb_record(dst)
    if rec is None:
        raise DaeError(f"{src.name}: the conversion wrote no readable GLB")
    return rec


def prepare(src: Path | str, cache_dir: Path | str, key: str) -> dict[str, Any]:
    """The 3D view's scene entry for a DAE: ``{kind: mesh, role: .dae, files: {glb},
    count, bounds, up: "y", version}``, converting when no matching GLB exists (module
    docstring). ``key`` names the DAE's identity (path, size, mtime) in the cache."""
    src = Path(src)
    stamp = source_stamp(src)
    cache = cache_folder(cache_dir)

    def make() -> dict[str, Any]:
        side = converted_path(src)
        rec = _matches(side, stamp)
        glb = side
        if rec is None:
            cache.mkdir(parents=True, exist_ok=True)
            fresh = cache / f"{key}.{os.getpid()}.convert.glb"
            try:
                _convert_bounded(src, fresh, stamp)
                try:
                    write_atomic(fresh.read_bytes(), side)
                except OSError:  # the DAE's folder cannot take it: the cache can
                    glb = cache / f"{key}{CONVERTED_TAIL}"
                    os.replace(fresh, glb)
            finally:
                fresh.unlink(missing_ok=True)
            rec = glb_record(glb)
            if rec is None:
                raise DaeError(f"{src.name}: the conversion wrote no readable GLB")
        scene: dict[str, Any] = {"kind": "mesh", "role": ".dae", "files": {"glb": str(glb)}}
        scene["count"] = rec.get("count", 0)
        if rec.get("bounds") is not None:
            scene["bounds"] = rec["bounds"]
        scene["up"] = "y"
        scene["version"] = version_of(stamp)
        return scene

    def valid(scene: dict[str, Any]) -> bool:
        glb = Path((scene.get("files") or {}).get("glb", ""))
        return _matches(glb, stamp) is not None

    return cached(cache / f"{key}.dae.json", make, valid)


__all__ = [
    "CONVERTED_SUFFIX",
    "CONVERTED_TAIL",
    "CONVERTER_VERSION",
    "CONVERT_TIMEOUT_S",
    "DAE_MAX_BYTES",
    "DaeAsset",
    "DaeError",
    "convert",
    "converted_path",
    "glb_record",
    "is_converted",
    "prepare",
    "read_asset",
    "scene_info",
    "source_stamp",
    "to_gltf_frame",
    "version_of",
    "with_record",
]


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv[1:]))
