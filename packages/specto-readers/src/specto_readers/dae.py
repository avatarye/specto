"""COLLADA (``.dae``) for the 3D view (SPEC-42, ADR 0028): Babylon has no COLLADA loader,
so resolve converts a DAE to a GLB that Babylon's glTF loader draws.

- **Converter**: trimesh (MIT) reading through pycollada (BSD-3-Clause), both pure Python
  on numpy; textures are decoded with Pillow and embedded in the GLB. pycollada parses
  with the standard library's ElementTree (lxml is not installed; it would also resolve
  external entities). trimesh keeps each instance's accumulated node transform (the
  hierarchy itself is flattened under the root), triangles and polylists, normals, UVs,
  vertex colours and the common profile's materials (diffuse colour or texture, emission,
  transparency, shininess as roughness). Cameras, lights, animation and skins are
  dropped. Texture files are found by a resolver of our own (``_GuardedResolver``):
  an image name goes through ``specto_core.pathguard.reference_refusal`` before
  trimesh may resolve or stat it, so a network or device path in an ``<init_from>``
  (``\\\\host\\x``, ``//host/x``, ``\\??\\UNC\\host\\x``, ``%5C%5Chost%5Cx``) never
  makes the child connect out (SPEC-69); the model converts without that texture.
- **Units and up axis**: neither trimesh nor pycollada applies them, so the conversion
  does: ``<unit meter>`` scales to metres and ``<up_axis>`` turns the scene to glTF's Y up
  with a rotation (never a mirror): Z_UP maps (x, y, z) to (x, z, -y), X_UP to
  (-y, x, z), as COLLADA's axis table says (right, up, in). The result is metres, Y up,
  so the scene entry declares ``up: "y"`` and the view's ``up`` param leaves it alone.
- **Bounded**: a DAE over ``DAE_MAX_BYTES`` is refused, and the conversion runs in an
  isolated child Python (``python -I -m specto_readers.dae``, from the cache folder) that
  is killed with its whole process tree after ``CONVERT_TIMEOUT_S``; only the tail of its
  output is read back.
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

import contextlib
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from specto_core.pathguard import reference_refusal

from .glb import GLTF_UP
from .scenecache import (
    CONVERT_LOG_SUFFIX,
    CONVERT_TEMP_SUFFIX,
    cache_folder,
    cached,
    retry_sharing,
    sweep_stale,
    unique_temp,
    write_atomic,
)

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
        with retry_sharing(lambda: path.open("rb")) as fh:
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

    class _GuardedResolver(trimesh.resolvers.FilePathResolver):
        """trimesh's file resolver with the path guard in front: a texture name that is a
        network or device path is refused by its text before ``absolute`` may resolve or
        stat it (SPEC-69). trimesh drops a texture it cannot load and goes on."""

        def absolute(self, name: str) -> Path:
            why = reference_refusal(name)
            if why is not None:
                raise ValueError(f"{name!r} is {why}: not looked for")
            return super().absolute(name)

        def get(self, name: str) -> bytes:
            why = reference_refusal(name)  # the whole name, before any candidate of it
            if why is not None:
                raise ValueError(f"{name!r} is {why}: not looked for")
            return super().get(name)

        def namespaced(self, namespace: str) -> _GuardedResolver:
            return _GuardedResolver(
                os.path.join(self.parent, namespace), allow_anywhere=self.allow_anywhere
            )

    asset = read_asset(src)
    try:
        # process=False: vertices stay as the file has them (no merging across UV seams);
        # textures through the guarded resolver (the file's folder is its root)
        scene = trimesh.load_scene(
            str(src), file_type="dae", process=False, resolver=_GuardedResolver(str(src))
        )
    except Exception as e:  # pycollada and trimesh raise many kinds
        raise DaeError(f"{src.name}: {type(e).__name__}: {e}") from e
    if not scene.geometry:
        raise DaeError(f"{src.name}: no geometry to show")
    scene.apply_transform(to_gltf_frame(asset))
    info = scene_info(scene)
    return with_record(scene.export(file_type="glb"), {"source": stamp, **info})


def _child_main(argv: list[str]) -> int:
    """``python -I -m specto_readers.dae <src> <out> <stamp>``: the GLB straight to ``out``,
    a path the parent owns and removes; one JSON line on stdout says how it went."""
    src, out = Path(argv[0]), Path(argv[1])
    try:
        data = _convert_here(src, json.loads(argv[2]))
    except DaeError as e:
        print(json.dumps({"error": str(e)}))
        return 2
    out.write_bytes(data)
    print(json.dumps({"ok": True}))
    return 0


#: how much of the child's output is read back (its tail)
OUTPUT_TAIL_BYTES = 8 * 1024


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """The child and everything it started (a venv launcher runs the real Python as its
    own child): ``taskkill /T`` on Windows, the child's process group elsewhere."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    proc.kill()
    proc.wait()


def _tail(log: Path) -> list[str]:
    try:
        with log.open("rb") as fh:
            fh.seek(max(0, log.stat().st_size - OUTPUT_TAIL_BYTES))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip()]


def _convert_bounded(src: Path, out: Path, stamp: dict[str, int]) -> None:
    """Convert in a child Python (isolated: ``-I``, run from ``out``'s folder, so nothing in
    the data folder or the current directory is imported), killed with its whole tree
    after ``CONVERT_TIMEOUT_S``. The GLB lands at ``out``, which the caller owns and
    removes. Raises ``DaeError``."""
    size = stamp["size"]
    if size > DAE_MAX_BYTES:
        raise DaeError(
            f"{src.name}: {size / 2**20:.0f} MB; the 3d view converts DAE files up to "
            f"{DAE_MAX_BYTES / 2**20:.0f} MB"
        )
    cmd = [
        sys.executable, "-I", "-m", "specto_readers.dae",
        str(src.resolve()), str(out.resolve()), json.dumps(stamp),
    ]  # fmt: skip
    # the log takes out's unique name with the converter's log ending (the sweep knows it)
    log = out.with_name(out.name.removesuffix(CONVERT_TEMP_SUFFIX) + CONVERT_LOG_SUFFIX)
    win = sys.platform == "win32"
    try:
        with log.open("wb") as fh:
            proc = subprocess.Popen(
                cmd,
                cwd=str(out.parent),
                stdin=subprocess.DEVNULL,
                stdout=fh,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if win else 0,
                start_new_session=not win,
            )
            try:
                code = proc.wait(timeout=CONVERT_TIMEOUT_S)
            except subprocess.TimeoutExpired as e:
                _kill_tree(proc)
                raise DaeError(
                    f"{src.name}: conversion took longer than {CONVERT_TIMEOUT_S:g} s, stopped"
                ) from e
        lines = _tail(log)
    finally:
        with contextlib.suppress(OSError):
            log.unlink(missing_ok=True)
    answer: dict[str, Any] = {}
    for line in reversed(lines):
        try:
            found = json.loads(line)
        except ValueError:
            continue
        if isinstance(found, dict):
            answer = found
            break
    if code != 0 or not answer.get("ok"):
        if answer.get("error"):
            raise DaeError(str(answer["error"]))
        raise DaeError(f"{src.name}: conversion failed: {lines[-1] if lines else code}")


def convert(src: Path | str, dst: Path | str) -> dict[str, Any]:
    """Convert one DAE to a GLB at ``dst`` (bounded; written whole or not at all); returns
    the GLB's record: ``{source, count, bounds}``."""
    src, dst = Path(src), Path(dst)
    fresh = unique_temp(dst.parent, dst.name, CONVERT_TEMP_SUFFIX)
    try:
        _convert_bounded(src, fresh, source_stamp(src))
        os.replace(fresh, dst)
    finally:
        fresh.unlink(missing_ok=True)
    rec = glb_record(dst)
    if rec is None:
        raise DaeError(f"{src.name}: the conversion wrote no readable GLB")
    return rec


#: the cache record of a conversion kept in the cache only (``prepare(beside=False)``),
#: apart from the one beside the source, so the two never share a GLB
CACHE_ONLY_TAG = ".cached"


def prepare(
    src: Path | str, cache_dir: Path | str, key: str, *, beside: bool = True
) -> dict[str, Any]:
    """The 3D view's scene entry for a DAE: ``{kind: mesh, role: .dae, files: {glb},
    count, bounds, up: "y", version}``, converting when no matching GLB exists (module
    docstring). ``key`` names the DAE's identity (path, size, mtime) in the cache.
    ``beside`` False (the urdf view's DAE outside the project, ADR 0029) never reads or
    writes a GLB beside the source: the conversion lives in the cache only."""
    src = Path(src)
    stamp = source_stamp(src)
    cache = cache_folder(cache_dir)

    def make() -> dict[str, Any]:
        in_cache = cache / f"{key}{CONVERTED_TAIL}"
        side = converted_path(src) if beside else in_cache
        glb, rec = side, _matches(side, stamp)
        if beside and rec is None and (cached_rec := _matches(in_cache, stamp)) is not None:
            glb, rec = in_cache, cached_rec  # an earlier run fell back to the cache
        if rec is None:
            cache.mkdir(parents=True, exist_ok=True)
            sweep_stale(cache)
            fresh = unique_temp(cache, key, CONVERT_TEMP_SUFFIX)
            try:
                _convert_bounded(src, fresh, stamp)
                try:
                    write_atomic(fresh.read_bytes(), side)
                except OSError:
                    if _matches(side, stamp) is None:
                        # the DAE's folder cannot take it (and nobody else wrote it): the
                        # cache can
                        glb = in_cache
                        retry_sharing(lambda: os.replace(fresh, in_cache))
            finally:
                fresh.unlink(missing_ok=True)
            rec = glb_record(glb)
            if rec is None:
                raise DaeError(f"{src.name}: the conversion wrote no readable GLB")
        scene: dict[str, Any] = {"kind": "mesh", "role": ".dae", "files": {"glb": str(glb)}}
        scene["count"] = rec.get("count", 0)
        if rec.get("bounds") is not None:
            scene["bounds"] = rec["bounds"]
        scene["up"] = GLTF_UP
        scene["version"] = version_of(stamp)
        return scene

    def valid(scene: dict[str, Any]) -> bool:
        glb = Path((scene.get("files") or {}).get("glb", ""))
        return _matches(glb, stamp) is not None

    # one conversion of a DAE at a time in this process: a second thread waits and reads
    # what the first wrote (other processes meet it through the GLB's record)
    with _key_lock(key):
        record = cache / f"{key}{'' if beside else CACHE_ONLY_TAG}.dae.json"
        return cached(record, make, valid)


_KEY_LOCKS: dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _key_lock(key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        return _KEY_LOCKS.setdefault(key, threading.Lock())


__all__ = [
    "CACHE_ONLY_TAG",
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
