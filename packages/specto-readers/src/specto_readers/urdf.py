"""The urdf view's robot model (SPEC-43.1, ADR 0028): a URDF parsed into the host/page
contract's model (ADR 0028, "The host and page contract"), with its mesh and
texture references found by Specto's own ``package://`` resolver.

``build_robot(path, ...)`` is the whole job, picklable for the process pool: yourdfpy
parses (``build_scene_graph=False, load_meshes=False``: it only reads the XML, no mesh is
loaded and no scene graph built), and each ``<mesh filename>`` and ``<texture filename>``
goes through ``PackageResolver``, in the design's order:

1. ``package_paths`` (the view's ``params.package_paths``): ``{name: folder}``, or a list
   of folders each searched for the package;
2. the nearest ``package.xml`` whose ``<name>`` matches: walking up from the URDF, then
   searching the project's folders (``search_roots``) to a bounded depth;
3. ``ROS_PACKAGE_PATH`` (each entry searched like a folder of step 1) and
   ``AMENT_PREFIX_PATH`` (``<prefix>/share/<name>``);
4. a relative filename resolves next to the URDF; ``file://`` and absolute paths
   resolve directly.

A package is never guessed by stripping the ``package://<name>/`` prefix (a wrong file
picked silently is worse than a placeholder), and ``http(s)://`` is never fetched
(offline rule). What is not found keeps its geometry with ``url: None`` (the page draws a
placeholder) and is listed in the model's ``missing`` with its cause; ``missing_notes``
groups the causes into the view's health notes.

The model leaves the files out: a mesh geometry carries ``file`` (a role, ``m0``,
``m1``, ...) and a material ``texture_file``, both keys of the result's ``files``
(``{role: absolute path}``); the host serves those through the items host and swaps
each role for its URL. A DAE goes as its SPEC-42 sidecar ``<name>.dae.glb`` when one
next to it is as new as the DAE, else as the DAE itself (``ext: "dae"``).
"""

from __future__ import annotations

import math
import os
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

#: mesh file extension -> the Babylon loader the page uses (``ext`` in the model)
MESH_EXTS: dict[str, str] = {
    ".stl": "stl",
    ".obj": "obj",
    ".glb": "glb",
    ".gltf": "gltf",
    ".dae": "dae",
}
SUPPORTED_NOTE = "STL, OBJ, DAE, glTF"
DAE_SIDECAR = ".glb"  # SPEC-42: <name>.dae.glb next to the DAE
#: how deep the project's folders and a package_paths / ROS_PACKAGE_PATH folder are
#: searched for a package.xml, and how many folders at most (bounded on a big tree)
SEARCH_DEPTH = 5
SEARCH_MAX_DIRS = 4000
SKIP_DIRS = frozenset({".git", ".hg", ".svn", ".specto", "node_modules", "__pycache__"})
NOT_FETCHED = "http(s) is never fetched (offline)"
PACKAGE_HINT = "set `params.package_paths`"

Vec = list[float]


class UrdfError(Exception):
    """The file is not a URDF Specto can read."""


@dataclass(frozen=True)
class Resolution:
    """Where a URDF filename points: ``path`` (absolute) or None with ``cause``;
    ``via`` names the step that found it (``package_paths``, ``package.xml``,
    ``ROS_PACKAGE_PATH``, ``AMENT_PREFIX_PATH``, ``relative``, ``file``)."""

    path: str | None
    cause: str | None = None
    via: str = ""
    package: str | None = None


def package_name_of(folder: Path) -> str | None:
    """The ``<name>`` of ``folder/package.xml``, or None."""
    xml = folder / "package.xml"
    try:
        if not xml.is_file():
            return None
        name = ET.parse(xml).getroot().findtext("name")
    except (OSError, ET.ParseError):
        return None
    return name.strip() if name else None


def _search(root: Path, name: str, depth: int = SEARCH_DEPTH) -> Path | None:
    """The shallowest folder under ``root`` (``root`` included) whose package.xml names
    ``name``, breadth first, bounded in depth and folder count."""
    level = [root]
    seen = 0
    for _ in range(depth + 1):
        nxt: list[Path] = []
        for d in level:
            if package_name_of(d) == name:
                return d
            seen += 1
            if seen > SEARCH_MAX_DIRS:
                return None
            try:
                children = sorted(e for e in d.iterdir() if e.is_dir() and e.name not in SKIP_DIRS)
            except OSError:
                continue
            nxt.extend(c for c in children if not c.name.startswith("."))
        level = nxt
        if not level:
            break
    return None


def _in_folder(folder: Path, name: str) -> Path | None:
    """A package ``name`` inside a folder of packages: ``folder`` itself when its
    package.xml names it, ``folder/<name>`` (the ROS_PACKAGE_PATH layout), else a
    package.xml naming it below ``folder``."""
    if not folder.is_dir():
        return None
    if package_name_of(folder) == name:
        return folder
    direct = folder / name
    if direct.is_dir():
        return direct
    return _search(folder, name)


class PackageResolver:
    """Specto's resolver for URDF filenames (module docstring). Packages found once are
    remembered (``packages``: name -> (folder, via))."""

    def __init__(
        self,
        urdf_path: str | Path,
        *,
        package_paths: Mapping[str, str] | Iterable[str] | None = None,
        search_roots: Iterable[str | Path] = (),
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.urdf = Path(urdf_path).resolve()
        if isinstance(package_paths, Mapping):
            self.by_name = {str(k): Path(str(v)) for k, v in package_paths.items()}
            self.folders: list[Path] = []
        else:
            self.by_name = {}
            self.folders = [Path(str(p)) for p in (package_paths or [])]
        self.search_roots = [Path(r) for r in search_roots]
        env = os.environ if environ is None else environ
        self.ros_paths = [Path(p) for p in env.get("ROS_PACKAGE_PATH", "").split(os.pathsep) if p]
        self.ament_paths = [
            Path(p) for p in env.get("AMENT_PREFIX_PATH", "").split(os.pathsep) if p
        ]
        self.packages: dict[str, tuple[Path, str]] = {}
        self._unfound: set[str] = set()

    def find_package(self, name: str) -> tuple[Path, str] | None:
        if name in self.packages:
            return self.packages[name]
        if name in self._unfound:
            return None
        hit = self._find(name)
        if hit is None:
            self._unfound.add(name)
            return None
        self.packages[name] = (hit[0].resolve(), hit[1])
        return self.packages[name]

    def _find(self, name: str) -> tuple[Path, str] | None:
        # 1. params.package_paths
        if name in self.by_name:
            return self.by_name[name], "package_paths"
        for folder in self.folders:
            hit = _in_folder(folder, name)
            if hit is not None:
                return hit, "package_paths"
        # 2. the nearest package.xml: up from the URDF, then the project's folders
        for d in self.urdf.parents:
            if package_name_of(d) == name:
                return d, "package.xml"
        for root in self.search_roots:
            hit = _search(root, name)
            if hit is not None:
                return hit, "package.xml"
        # 3. the ROS environment
        for folder in self.ros_paths:
            hit = _in_folder(folder, name)
            if hit is not None:
                return hit, "ROS_PACKAGE_PATH"
        for prefix in self.ament_paths:
            share = prefix / "share" / name
            if share.is_dir():
                return share, "AMENT_PREFIX_PATH"
        return None

    def resolve(self, uri: str) -> Resolution:
        text = uri.strip()
        low = text.lower()
        if low.startswith(("http://", "https://")):
            return Resolution(None, NOT_FETCHED)
        if low.startswith("package://"):
            rest = text[len("package://") :]
            name, _, rel = rest.partition("/")
            if not name:
                return Resolution(None, f"no package name in `{uri}`")
            found = self.find_package(name)
            if found is None:
                return Resolution(None, f"package `{name}` not found", package=name)
            folder, via = found
            target = (folder / rel).resolve()
            if not target.is_file():
                return Resolution(None, f"`{rel}` is not in package `{name}`", via, name)
            return Resolution(str(target), None, via, name)
        if low.startswith("file://"):
            parsed = urlparse(text)
            raw = unquote(parsed.path)
            if os.name == "nt" and raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
                raw = raw[1:]  # file:///C:/x -> C:/x
            target = Path(raw)
            via = "file"
        elif os.path.isabs(text):
            target, via = Path(text), "file"
        else:
            target, via = self.urdf.parent / text, "relative"
        target = target.resolve()
        if not target.is_file():
            return Resolution(None, f"not found: {target}", via)
        return Resolution(str(target), None, via)


# -- the model -----------------------------------------------------------------------------


def _floats(values: Any, n: int, default: float = 0.0) -> Vec:
    if values is None:
        return [default] * n
    try:
        out = [float(v) for v in values]
    except TypeError:  # a scalar (a uniform mesh scale)
        out = [float(values)] * n
    return (out + [default] * n)[:n]


def _origin(matrix: Any) -> dict[str, Vec]:
    """A 4x4 homogeneous transform as URDF's ``{xyz, rpy}`` (fixed-axis roll, pitch,
    yaw: R = Rz(yaw) Ry(pitch) Rx(roll))."""
    if matrix is None:
        return {"xyz": [0.0, 0.0, 0.0], "rpy": [0.0, 0.0, 0.0]}
    m = [[float(matrix[i][j]) for j in range(4)] for i in range(4)]
    xyz = [m[0][3], m[1][3], m[2][3]]
    sy = math.hypot(m[0][0], m[1][0])
    if sy > 1e-9:
        roll = math.atan2(m[2][1], m[2][2])
        pitch = math.atan2(-m[2][0], sy)
        yaw = math.atan2(m[1][0], m[0][0])
    else:  # gimbal lock: pitch is +-90 degrees, roll and yaw share one angle
        roll = math.atan2(-m[1][2], m[1][1])
        pitch = math.atan2(-m[2][0], sy)
        yaw = 0.0
    return {"xyz": [_clean(v) for v in xyz], "rpy": [_clean(v) for v in (roll, pitch, yaw)]}


def _clean(v: float) -> float:
    """-0.0 and float noise around zero as 0.0 (stable JSON for the page and tests)."""
    return 0.0 if abs(v) < 1e-12 else float(v)


class _Builder:
    def __init__(self, resolver: PackageResolver) -> None:
        self.resolver = resolver
        self.files: dict[str, str] = {}
        self._roles: dict[str, str] = {}  # path -> role
        self.missing: list[dict[str, Any]] = []

    def role_for(self, path: str) -> str:
        role = self._roles.get(path)
        if role is None:
            role = f"m{len(self._roles)}"
            self._roles[path] = role
            self.files[role] = path
        return role

    def geometry(self, geo: Any, link: str, kind: str) -> dict[str, Any] | None:
        if geo is None:
            return None
        if geo.box is not None:
            return {"type": "box", "size": _floats(geo.box.size, 3)}
        if geo.cylinder is not None:
            return {
                "type": "cylinder",
                "radius": float(geo.cylinder.radius),
                "length": float(geo.cylinder.length),
            }
        if geo.sphere is not None:
            return {"type": "sphere", "radius": float(geo.sphere.radius)}
        if geo.mesh is not None:
            return self.mesh(str(geo.mesh.filename), geo.mesh.scale, link, kind)
        return None

    def mesh(self, uri: str, scale: Any, link: str, kind: str) -> dict[str, Any]:
        ext = MESH_EXTS.get(Path(urlparse(uri).path or uri).suffix.lower())
        out: dict[str, Any] = {
            "type": "mesh",
            "url": None,
            "ext": ext,
            "scale": _floats(scale, 3, 1.0),
        }
        hit = self.resolver.resolve(uri)
        cause = hit.cause
        path = hit.path
        if path is not None:
            suffix = Path(path).suffix.lower()
            if suffix not in MESH_EXTS:
                fmt = suffix or "(none)"
                cause, path = f"mesh format {fmt} is not supported ({SUPPORTED_NOTE})", None
            else:
                out["ext"] = MESH_EXTS[suffix]
                if suffix == ".dae":
                    path, out["ext"] = dae_source(path)
        if path is None:
            self.missing.append({"link": link, "kind": kind, "uri": uri, "cause": cause})
            return out
        out["file"] = self.role_for(path)
        return out

    def material(self, mat: Any, named: dict[str, Any]) -> dict[str, Any] | None:
        if mat is None:
            return None
        glob = named.get(mat.name) if mat.name else None
        color = mat.color if mat.color is not None else (glob.color if glob else None)
        texture = mat.texture if mat.texture is not None else (glob.texture if glob else None)
        out: dict[str, Any] = {
            "name": mat.name,
            "color": _floats(color.rgba, 4) if color is not None else None,
            "texture_url": None,
        }
        if texture is not None and texture.filename:
            hit = self.resolver.resolve(str(texture.filename))
            if hit.path is not None:
                out["texture_file"] = self.role_for(hit.path)
        return out


def dae_source(path: str) -> tuple[str, str]:
    """A DAE's file for the page: its SPEC-42 sidecar ``<name>.dae.glb`` when one next to
    it is at least as new, else the DAE itself."""
    sidecar = Path(path + DAE_SIDECAR)
    try:
        if sidecar.is_file() and sidecar.stat().st_mtime >= Path(path).stat().st_mtime:
            return str(sidecar), "glb"
    except OSError:
        pass
    return path, "dae"


def _parse(path: Path) -> Any:
    try:
        text = path.read_bytes()
    except OSError as e:
        raise UrdfError(f"cannot read {path.name}: {e.strerror or e}") from e
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise UrdfError(f"{path.name} is not well-formed XML: {e}") from e
    if root.tag != "robot":
        raise UrdfError(f"{path.name} is not a URDF (its root element is <{root.tag}>)")
    import yourdfpy  # parse only (module docstring)

    try:
        return yourdfpy.URDF.load(str(path), build_scene_graph=False, load_meshes=False)
    except Exception as e:  # yourdfpy raises bare ValueError / AttributeError on bad input
        raise UrdfError(f"{path.name}: {type(e).__name__}: {e}") from e


def build_robot(
    path: str | Path,
    *,
    package_paths: Mapping[str, str] | Iterable[str] | None = None,
    search_roots: Iterable[str | Path] = (),
    environ: Mapping[str, str] | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Parse ``path`` and resolve its files: ``{model, files, packages, actuated,
    parse_ms}`` (module docstring). Raises ``UrdfError`` for a file that is not a
    readable URDF."""
    path = Path(path)
    start = time.perf_counter()
    if progress:
        progress(0.0, "parsing")
    urdf = _parse(path)
    robot = urdf.robot
    resolver = PackageResolver(
        path, package_paths=package_paths, search_roots=search_roots, environ=environ
    )
    b = _Builder(resolver)
    named = {m.name: m for m in robot.materials if m.name}
    links = []
    for lk in robot.links:
        links.append(
            {
                "name": lk.name,
                "visuals": [
                    {
                        "origin": _origin(v.origin),
                        "geometry": b.geometry(v.geometry, lk.name, "visual"),
                        "material": b.material(v.material, named),
                    }
                    for v in lk.visuals
                ],
                "collisions": [
                    {
                        "origin": _origin(c.origin),
                        "geometry": b.geometry(c.geometry, lk.name, "collision"),
                    }
                    for c in lk.collisions
                ],
                "inertial": _inertial(lk.inertial),
            }
        )
    joints = [_joint(j) for j in robot.joints]
    children = {j["child"] for j in joints}
    root = next((lk["name"] for lk in links if lk["name"] not in children), None)
    model = {
        "name": robot.name,
        "root": root,
        "links": links,
        "joints": joints,
        "missing": b.missing,
    }
    if progress:
        progress(1.0, f"{len(links)} links, {len(joints)} joints")
    return {
        "model": model,
        "files": b.files,
        "packages": {
            name: {"path": str(folder), "via": via}
            for name, (folder, via) in resolver.packages.items()
        },
        "actuated": [j["name"] for j in joints if j["type"] in ACTUATED and j["mimic"] is None],
        # parsing and resolving, measured (SPEC-43: parse times measured in slice 1)
        "parse_ms": round((time.perf_counter() - start) * 1000.0, 3),
    }


#: joint types a slider moves (floating and planar get none, ADR 0028)
ACTUATED = ("revolute", "continuous", "prismatic")


def _inertial(inertial: Any) -> dict[str, Any] | None:
    if inertial is None:
        return None
    m = inertial.inertia
    tensor = [[float(m[i][j]) for j in range(3)] for i in range(3)] if m is not None else None
    return {
        "origin": _origin(inertial.origin),
        "mass": float(inertial.mass or 0.0),
        "inertia": {
            "ixx": tensor[0][0] if tensor else 0.0,
            "ixy": tensor[0][1] if tensor else 0.0,
            "ixz": tensor[0][2] if tensor else 0.0,
            "iyy": tensor[1][1] if tensor else 0.0,
            "iyz": tensor[1][2] if tensor else 0.0,
            "izz": tensor[2][2] if tensor else 0.0,
        },
    }


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _joint(j: Any) -> dict[str, Any]:
    limit = None
    if j.limit is not None:
        limit = {
            "lower": _num(j.limit.lower),
            "upper": _num(j.limit.upper),
            "effort": _num(j.limit.effort),
            "velocity": _num(j.limit.velocity),
        }
    mimic = None
    if j.mimic is not None:
        mimic = {
            "joint": str(j.mimic.joint),
            "multiplier": _num(j.mimic.multiplier, 1.0),
            "offset": _num(j.mimic.offset),
        }
    dynamics = None
    if j.dynamics is not None:
        # yourdfpy keeps these as the attribute strings
        dynamics = {"damping": _num(j.dynamics.damping), "friction": _num(j.dynamics.friction)}
    return {
        "name": j.name,
        "type": j.type,
        "parent": j.parent,
        "child": j.child,
        "origin": _origin(j.origin),
        "axis": _floats(j.axis, 3) if j.axis is not None else [1.0, 0.0, 0.0],
        "limit": limit,
        "mimic": mimic,
        "dynamics": dynamics,
    }


def missing_notes(missing: Iterable[Mapping[str, Any]]) -> list[str]:
    """The view's health notes for the missing meshes: one per cause, most meshes first
    (ties in order of first appearance); a package not found says how to fix it."""
    counts: dict[str, int] = {}
    for m in missing:
        cause = str(m.get("cause") or "unknown")
        counts[cause] = counts.get(cause, 0) + 1
    order = sorted(counts, key=lambda c: -counts[c])  # stable: first seen wins a tie
    notes = []
    for cause in order:
        n = counts[cause]
        line = f"{n} mesh{'es' if n != 1 else ''} missing: {cause}"
        if cause.startswith("package `") and cause.endswith("not found"):
            line += f"; {PACKAGE_HINT}"
        notes.append(line)
    return notes


__all__ = [
    "ACTUATED",
    "MESH_EXTS",
    "PackageResolver",
    "Resolution",
    "UrdfError",
    "build_robot",
    "dae_source",
    "missing_notes",
    "package_name_of",
]
