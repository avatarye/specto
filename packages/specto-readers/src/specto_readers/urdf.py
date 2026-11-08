"""The urdf view's robot model (SPEC-43.1, ADR 0029): a URDF parsed into the host/page
contract's model (ADR 0029, "The host and page contract"), with its mesh and texture
references found by Specto's own ``package://`` resolver.

``parse_urdf(path)`` reads the XML with the standard library (``xml.etree``): no
third-party parser, since the maintained one (yourdfpy) needs lxml, whose binary wheels
bundle LGPL-2.1 iconv (ADR 0029). A URDF never needs a DTD, so a document with a
``<!DOCTYPE`` or an entity declaration is refused before it is parsed (no entity
expansion). Numbers are coerced strictly (a bad ``xyz`` is an error naming its element),
``rpy`` is kept exactly as written, a visual's named material takes the robot's global
material of that name, and mimic joints keep their ``joint``, ``multiplier`` and
``offset``.

``build_robot(path, ...)`` is the whole job, picklable for the process pool: it parses,
then sends each ``<mesh filename>`` and ``<texture filename>`` through
``PackageResolver``, in the design's order:

1. ``package_paths`` (the view's ``params.package_paths``): ``{name: folder}``, or a list
   of folders each searched for the package;
2. the nearest ``package.xml`` whose ``<name>`` matches: walking up from the URDF, then
   searching the project's folders (``search_roots``) to a bounded depth;
3. ``ROS_PACKAGE_PATH`` (each entry searched like a folder of step 1) and
   ``AMENT_PREFIX_PATH`` (``<prefix>/share/<name>``);
4. a relative filename resolves next to the URDF; ``file://`` and absolute paths
   resolve directly.

Before any filesystem call a reference's text goes through ``specto_core.pathguard``: a
network (UNC) path, a ``file://`` URI naming a host, a device path or name and an NTFS
stream name are refused, so parsing an untrusted URDF never connects out. A package is
never guessed by stripping the ``package://<name>/`` prefix, a ``package://`` path never
leaves its package, and ``http(s)://`` is never fetched (offline rule).

What may be served (ADR 0029 decision 6 and amendment 18): a file whose resolved path
lies under the project (``search_roots``; the URDF's own folder when none is given) or a
package folder this parse resolved, of a mesh type (STL, OBJ, glTF, GLB) or an image
type, a regular file of at most ``MAX_SERVED_BYTES``. A DAE goes as the GLB that
SPEC-42's ``dae.prepare`` returns, converted into the cache, never beside a source
outside the project. Per parse at most ``MAX_REFERENCES`` references and
``MAX_DAE_CONVERSIONS`` conversions are taken, and the package search shares one folder
budget. Anything else keeps its geometry with ``url: None`` (the page draws a
placeholder) and is listed in the model's ``missing`` with its cause; ``missing_notes``
groups the causes into the view's health notes.

The model leaves the files out: a mesh geometry carries ``file`` (a role, ``m0``,
``m1``, ...) and a material ``texture_file``, both keys of the result's ``files``
(``{role: absolute path}``); the host serves those through the items host and swaps
each role for its URL. A mesh keeps its URDF reference as ``uri``.
"""

from __future__ import annotations

import math
import os
import re
import stat
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.parsers import expat

from specto_core.pathguard import file_uri_path, path_refusal, under

from .mesh_assets import BUFFER_EXTS, MATERIAL_EXTS, TEXTURE_EXTS

#: mesh file extension -> the Babylon loader the page uses (``ext`` in the model); only
#: these are ever served as a mesh (a DAE goes as the GLB SPEC-42 converts it to)
MESH_EXTS: dict[str, str] = {
    ".stl": "stl",
    ".obj": "obj",
    ".glb": "glb",
    ".gltf": "gltf",
}
SUPPORTED_NOTE = "STL, OBJ, glTF, DAE"
#: a DAE when no cache folder was given to convert it into (a caller other than the host)
DAE_NOTE = "DAE not converted: no cache folder"
#: what may be served next to a mesh (``specto://items/.../m<n>/<name>``): an OBJ's
#: material library and a glTF's buffers, and their textures
SIDE_EXTS: dict[str, frozenset[str]] = {
    "obj": MATERIAL_EXTS | TEXTURE_EXTS,
    "gltf": BUFFER_EXTS | TEXTURE_EXTS,
}
#: every type a robot's file may be served as (a mesh, or a texture)
SERVED_EXTS: frozenset[str] = frozenset(MESH_EXTS) | TEXTURE_EXTS
#: the largest mesh or texture served (a regular file; bounds a wrong reference)
MAX_SERVED_BYTES = 512 * 1024 * 1024
#: per parse: the mesh and texture references taken, and the DAEs converted (amendment 18)
MAX_REFERENCES = 2000
MAX_DAE_CONVERSIONS = 64
#: how deep the project's folders and a package_paths / ROS_PACKAGE_PATH folder are
#: searched for a package.xml, and how many folders one parse may look at in all (one
#: budget shared by every package name)
SEARCH_DEPTH = 5
SEARCH_MAX_DIRS = 4000
SKIP_DIRS = frozenset({".git", ".hg", ".svn", ".specto", "node_modules", "__pycache__"})
NOT_FETCHED = "http(s) is never fetched (offline)"
OUTSIDE = "outside the project and its packages"
PACKAGE_HINT = "set `params.package_paths`"
#: a ROS package name (REP 144 style): never a path, no dots, no spaces
PACKAGE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
#: URDF's joint types; floating and planar get no slider (ADR 0029)
JOINT_TYPES = ("revolute", "continuous", "prismatic", "fixed", "floating", "planar")
#: joint types a slider moves, and those of them whose limits bound it (a continuous
#: joint wraps)
ACTUATED = ("revolute", "continuous", "prismatic")
CLAMPED = ("revolute", "prismatic")
#: the largest URDF read (a robot description is kilobytes; this bounds a wrong pick)
MAX_BYTES = 64 * 1024 * 1024

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


# -- safe XML --------------------------------------------------------------------------------


class _Refused(Exception):
    pass


def _refuse(*_args: Any) -> None:
    raise _Refused


def safe_xml(data: bytes, name: str = "the file") -> ET.Element:
    """``data`` parsed by ``xml.etree``, after an expat pass that refuses any DOCTYPE
    or entity declaration (a URDF never needs one; nothing is expanded) and reports a
    document that is not well-formed. Raises ``UrdfError``."""
    probe = expat.ParserCreate()
    probe.StartDoctypeDeclHandler = _refuse
    probe.EntityDeclHandler = _refuse
    probe.UnparsedEntityDeclHandler = _refuse
    probe.ExternalEntityRefHandler = _refuse
    try:
        probe.Parse(data, True)
    except _Refused:
        raise UrdfError(
            f"{name} declares a DOCTYPE or an entity; a URDF needs neither, so Specto "
            "does not read it"
        ) from None
    except expat.ExpatError as e:
        raise UrdfError(f"{name} is not well-formed XML: {e}") from None
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:  # pragma: no cover - expat accepted it above
        raise UrdfError(f"{name} is not well-formed XML: {e}") from None


def servable(path: str | Path, allowed: frozenset[str] | set[str] | dict[str, str]) -> str | None:
    """Why ``path`` may not be served (None: it may): no device path or name and no
    stream name (``pathguard``; a network path is judged by where it lies, see
    ``under``), a type in ``allowed`` (suffixes), and a regular file of at most
    ``MAX_SERVED_BYTES``."""
    p = Path(path)
    refused = path_refusal(str(p), unc_ok=True)
    if refused:
        return f"{refused} is not served"
    suffix = p.suffix.lower()
    if suffix not in allowed:
        return f"type {suffix or '(none)'} is not served"
    try:
        st = p.stat()
    except OSError:
        return "not found"
    if not stat.S_ISREG(st.st_mode):
        return "not a regular file"
    if st.st_size > MAX_SERVED_BYTES:
        return f"over {MAX_SERVED_BYTES // (1024 * 1024)} MB"
    return None


def package_name_of(folder: Path) -> str | None:
    """The ``<name>`` of ``folder/package.xml``, or None (one over ``MAX_BYTES`` or
    unreadable is no package)."""
    xml = folder / "package.xml"
    try:
        if not xml.is_file() or xml.stat().st_size > MAX_BYTES:
            return None
        name = safe_xml(xml.read_bytes(), "package.xml").findtext("name")
    except (OSError, UrdfError):
        return None
    return name.strip() if name else None


@dataclass
class SearchBudget:
    """The folders one parse may look at while searching for packages, shared by every
    package name and every step that probes a folder (the walk up from the URDF, a
    folder of packages, the project's folders, the ROS environment; amendment 18);
    ``spent`` once a probe found it empty."""

    left: int = field(default_factory=lambda: SEARCH_MAX_DIRS)
    spent: bool = False

    def take(self) -> bool:
        if self.left <= 0:
            self.spent = True
            return False
        self.left -= 1
        return True


def _search(root: Path, name: str, budget: SearchBudget, depth: int = SEARCH_DEPTH) -> Path | None:
    """The shallowest folder under ``root`` (``root`` included) whose package.xml names
    ``name``, breadth first, bounded in depth and by the shared ``budget``; a folder
    reached twice (a symlink or junction loop) is walked once."""
    level = [root]
    visited: set[str] = set()
    for _ in range(depth + 1):
        nxt: list[Path] = []
        for d in level:
            try:
                real = os.path.realpath(d)
            except OSError:
                continue
            if real in visited:
                continue
            visited.add(real)
            if not budget.take():
                return None
            if package_name_of(d) == name:
                return d
            try:
                children = sorted(e for e in d.iterdir() if e.is_dir() and e.name not in SKIP_DIRS)
            except OSError:
                continue
            nxt.extend(c for c in children if not c.name.startswith("."))
        level = nxt
        if not level:
            break
    return None


def _named(folder: Path, name: str, budget: SearchBudget) -> bool:
    """``folder`` holds a package.xml naming ``name`` (one probe, charged to the budget)."""
    return budget.take() and package_name_of(folder) == name


def _in_folder(folder: Path, name: str, budget: SearchBudget) -> Path | None:
    """A package ``name`` inside a folder of packages: ``folder`` itself when its
    package.xml names it, ``folder/<name>`` (the ROS_PACKAGE_PATH layout) when it holds a
    package.xml naming it, else a package.xml naming it below ``folder``."""
    if not budget.take() or not folder.is_dir():
        return None
    if _named(folder, name, budget):
        return folder
    if _named(folder / name, name, budget):
        return folder / name
    return _search(folder, name, budget)


def _folders(texts: Iterable[str]) -> list[Path]:
    """Configured folders, without any the path guard refuses (a ``params.package_paths``
    from someone else's canvas may name a network share): never touched."""
    return [Path(t) for t in texts if t and path_refusal(t) is None]


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
            self.by_name = {
                str(k): Path(str(v))
                for k, v in package_paths.items()
                if path_refusal(str(v)) is None
            }
            self.folders: list[Path] = []
        else:
            self.by_name = {}
            self.folders = _folders(str(p) for p in (package_paths or []))
        self.search_roots = [Path(r) for r in search_roots]
        env = os.environ if environ is None else environ
        self.ros_paths = _folders(env.get("ROS_PACKAGE_PATH", "").split(os.pathsep))
        self.ament_paths = _folders(env.get("AMENT_PREFIX_PATH", "").split(os.pathsep))
        self.packages: dict[str, tuple[Path, str]] = {}
        self.budget = SearchBudget()
        self._unfound: set[str] = set()

    def find_package(self, name: str) -> tuple[Path, str] | None:
        """The package's ``(folder, via)``, or None. A package name is a plain name
        (``PACKAGE_NAME``: a letter, then letters, digits, ``_`` and ``-``), never a path:
        ``..`` or ``a.`` is no package."""
        if name in self.packages:
            return self.packages[name]
        if name in self._unfound or not PACKAGE_NAME.fullmatch(name):
            return None
        hit = self._find(name)
        if hit is None:
            self._unfound.add(name)
            return None
        self.packages[name] = (hit[0].resolve(), hit[1])
        return self.packages[name]

    def folder(self, name: str) -> str | None:
        """The package's folder, or None (xacro's ``$(find name)``, SPEC-43.3: a bound
        method of a picklable resolver, so it crosses into the xacro child process)."""
        hit = self.find_package(name)
        return str(hit[0]) if hit is not None else None

    def _find(self, name: str) -> tuple[Path, str] | None:
        # 1. params.package_paths
        if name in self.by_name:
            return self.by_name[name], "package_paths"
        for folder in self.folders:
            hit = _in_folder(folder, name, self.budget)
            if hit is not None:
                return hit, "package_paths"
        # 2. the nearest package.xml: up from the URDF, then the project's folders
        for d in self.urdf.parents:
            if _named(d, name, self.budget):
                return d, "package.xml"
        for root in self.search_roots:
            hit = _search(root, name, self.budget)
            if hit is not None:
                return hit, "package.xml"
        # 3. the ROS environment
        for folder in self.ros_paths:
            hit = _in_folder(folder, name, self.budget)
            if hit is not None:
                return hit, "ROS_PACKAGE_PATH"
        for prefix in self.ament_paths:
            share = prefix / "share" / name
            if _named(share, name, self.budget):
                return share, "AMENT_PREFIX_PATH"
        return None

    def resolve(self, uri: str, scope: Iterable[str | Path] | None = None) -> Resolution:
        """Where a URDF filename points (module docstring). The text is judged first, by
        ``pathguard``: a refused path is never handed to the filesystem. With ``scope``
        (the folders a served file may lie in) a file or relative reference outside it is
        ``OUTSIDE`` before anything asks whether it exists."""
        text = uri.strip()
        low = text.lower()
        if low.startswith(("http://", "https://")):
            return Resolution(None, NOT_FETCHED)
        if low.startswith("package://"):
            rest = text[len("package://") :]
            name, _, rel = rest.partition("/")
            if not name:
                return Resolution(None, f"no package name in `{uri}`")
            refused = path_refusal(rel)
            if refused:
                return Resolution(None, f"{refused} is never read")
            found = self.find_package(name)
            if found is None:
                cause = f"package `{name}` not found"
                if self.budget.spent:
                    cause += f" (the search budget of {SEARCH_MAX_DIRS} folders is spent)"
                return Resolution(None, cause, package=name)
            folder, via = found
            missing = f"not in package `{name}` (found by {via})"
            if os.path.isabs(rel) or Path(rel).drive or rel.startswith(("/", "\\")):
                return Resolution(None, missing, via, name)
            target = (folder / rel).resolve()
            try:
                target.relative_to(folder.resolve())  # package://pkg/../../x escapes it
            except ValueError:
                return Resolution(None, missing, via, name)
            if not target.is_file():
                return Resolution(None, missing, via, name)
            return Resolution(str(target), None, via, name)
        if low.startswith("file://"):
            raw, refused = file_uri_path(text)
            if refused or raw is None:
                return Resolution(None, f"{refused} is never read")
            target, via = Path(raw), "file"
        else:
            refused = path_refusal(text)
            if refused:
                return Resolution(None, f"{refused} is never read")
            if os.path.isabs(text):
                target, via = Path(text), "file"
            else:
                target, via = self.urdf.parent / text, "relative"
        target = target.resolve()
        if scope is not None and not under(target, scope):
            return Resolution(None, OUTSIDE, via)
        if not target.is_file():
            where = "next to the URDF" if via == "relative" else "at its absolute path"
            return Resolution(None, f"not found {where}", via)
        return Resolution(str(target), None, via)


# -- the parser ------------------------------------------------------------------------------

_SPACE = re.compile(r"\s+")


def _numbers(text: str | None, n: int | tuple[int, ...], what: str) -> Vec:
    """``text`` as ``n`` floats (``n`` may allow several counts: a mesh scale of 1 or 3);
    a missing, short, long or non-numeric value is an error naming ``what``."""
    counts = (n,) if isinstance(n, int) else n
    parts = [p for p in _SPACE.split((text or "").strip()) if p]
    try:
        out = [float(p) for p in parts]
    except ValueError:
        raise UrdfError(f"{what} is not numbers: {text!r}") from None
    if len(out) not in counts or not all(math.isfinite(v) for v in out):
        want = " or ".join(str(c) for c in counts)
        raise UrdfError(
            f"{what} needs {want} finite number{'s' if counts[-1] != 1 else ''}: {text!r}"
        )
    return out


def _number(el: ET.Element | None, attr: str, what: str, default: float | None = 0.0) -> float:
    """One float attribute of ``el``; ``default`` when the element or attribute is
    absent (None: required)."""
    raw = el.get(attr) if el is not None else None
    if raw is None:
        if default is None:
            raise UrdfError(f"{what} has no {attr}")
        return float(default)
    return _numbers(raw, 1, f"{what} {attr}")[0]


def _origin(parent: ET.Element, what: str) -> dict[str, Vec]:
    """``<origin xyz rpy>`` (each defaulting to zeros), exactly as written."""
    el = parent.find("origin")
    xyz = el.get("xyz") if el is not None else None
    rpy = el.get("rpy") if el is not None else None
    return {
        "xyz": _numbers(xyz, 3, f"{what} <origin xyz>") if xyz is not None else [0.0, 0.0, 0.0],
        "rpy": _numbers(rpy, 3, f"{what} <origin rpy>") if rpy is not None else [0.0, 0.0, 0.0],
    }


def _name(el: ET.Element, what: str) -> str:
    name = (el.get("name") or "").strip()
    if not name:
        raise UrdfError(f"a {what} has no name")
    return name


def _geometry(parent: ET.Element, what: str) -> dict[str, Any] | None:
    """The one shape of ``<geometry>``; None when there is no geometry or no shape in
    it (the shape is left out of the model)."""
    geo = parent.find("geometry")
    if geo is None:
        return None
    for shape in geo:
        tag = shape.tag
        if tag == "box":
            return {"type": "box", "size": _numbers(shape.get("size"), 3, f"{what} <box size>")}
        if tag == "cylinder":
            return {
                "type": "cylinder",
                "radius": _number(shape, "radius", f"{what} <cylinder>", None),
                "length": _number(shape, "length", f"{what} <cylinder>", None),
            }
        if tag == "sphere":
            return {"type": "sphere", "radius": _number(shape, "radius", f"{what} <sphere>", None)}
        if tag == "mesh":
            uri = (shape.get("filename") or "").strip()
            if not uri:
                raise UrdfError(f"{what} <mesh> has no filename")
            raw = shape.get("scale")
            scale = _numbers(raw, (1, 3), f"{what} <mesh scale>") if raw is not None else [1.0]
            ext = MESH_EXTS.get(Path(urlparse(uri).path or uri).suffix.lower())
            return {
                "type": "mesh",
                "url": None,
                "ext": ext,
                "scale": scale * 3 if len(scale) == 1 else scale,
                "uri": uri,
            }
    return None


def _material_parts(el: ET.Element, what: str) -> tuple[Vec | None, str | None]:
    color = el.find("color")
    rgba = (
        _numbers(color.get("rgba"), 4, f"{what} <color rgba>")
        if color is not None and color.get("rgba") is not None
        else None
    )
    texture = el.find("texture")
    tex = (texture.get("filename") or "").strip() if texture is not None else ""
    return rgba, tex or None


def _material(
    el: ET.Element | None, named: dict[str, tuple[Vec | None, str | None]], what: str
) -> dict[str, Any] | None:
    """A visual's ``<material>``: its own colour and texture, else the robot's global
    material of the same name."""
    if el is None:
        return None
    name = (el.get("name") or "").strip() or None
    rgba, tex = _material_parts(el, what)
    glob = named.get(name) if name else None
    if glob is not None:
        rgba = rgba if rgba is not None else glob[0]
        tex = tex if tex is not None else glob[1]
    out: dict[str, Any] = {"name": name, "color": rgba, "texture_url": None}
    if tex is not None:
        out["texture"] = tex
    return out


def _link(el: ET.Element, named: dict[str, tuple[Vec | None, str | None]]) -> dict[str, Any]:
    name = _name(el, "<link>")
    what = f"link {name}"
    visuals = []
    for v in el.findall("visual"):
        geo = _geometry(v, what)
        if geo is None:
            continue
        visuals.append(
            {
                "origin": _origin(v, what),
                "geometry": geo,
                "material": _material(v.find("material"), named, what),
            }
        )
    collisions = []
    for c in el.findall("collision"):
        geo = _geometry(c, what)
        if geo is not None:
            collisions.append({"origin": _origin(c, what), "geometry": geo})
    inertial = None
    ine = el.find("inertial")
    if ine is not None:
        tensor = ine.find("inertia")
        inertial = {
            "origin": _origin(ine, f"{what} <inertial>"),
            "mass": _number(ine.find("mass"), "value", f"{what} <mass>"),
            "inertia": {
                k: _number(tensor, k, f"{what} <inertia>")
                for k in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz")
            },
        }
    return {"name": name, "visuals": visuals, "collisions": collisions, "inertial": inertial}


def _joint(el: ET.Element) -> dict[str, Any]:
    name = _name(el, "<joint>")
    what = f"joint {name}"
    jtype = (el.get("type") or "").strip()
    if jtype not in JOINT_TYPES:
        raise UrdfError(f"{what} has type {jtype!r}; URDF's are {', '.join(JOINT_TYPES)}")
    ends = {}
    for end in ("parent", "child"):
        e = el.find(end)
        link = (e.get("link") or "").strip() if e is not None else ""
        if not link:
            raise UrdfError(f"{what} has no <{end} link>")
        ends[end] = link
    axis_el = el.find("axis")
    axis = (
        _numbers(axis_el.get("xyz"), 3, f"{what} <axis xyz>")
        if axis_el is not None and axis_el.get("xyz") is not None
        else [1.0, 0.0, 0.0]
    )
    lim = el.find("limit")
    limit = None
    if lim is not None:
        limit = {
            k: _number(lim, k, f"{what} <limit>") for k in ("lower", "upper", "effort", "velocity")
        }
    mim = el.find("mimic")
    mimic = None
    if mim is not None:
        master = (mim.get("joint") or "").strip()
        if not master:
            raise UrdfError(f"{what} <mimic> names no joint")
        mimic = {
            "joint": master,
            "multiplier": _number(mim, "multiplier", f"{what} <mimic>", 1.0),
            "offset": _number(mim, "offset", f"{what} <mimic>", 0.0),
        }
    dyn = el.find("dynamics")
    dynamics = None
    if dyn is not None:
        dynamics = {
            "damping": _number(dyn, "damping", f"{what} <dynamics>"),
            "friction": _number(dyn, "friction", f"{what} <dynamics>"),
        }
    return {
        "name": name,
        "type": jtype,
        "parent": ends["parent"],
        "child": ends["child"],
        "origin": _origin(el, what),
        "axis": axis,
        "limit": limit,
        "mimic": mimic,
        "dynamics": dynamics,
    }


def parse_urdf(
    source: str | Path | bytes | None = None,
    *,
    text: str | bytes | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """A URDF as the contract's model, files unresolved: mesh geometries carry ``uri``
    and ``url: None``, materials their ``texture`` filename; ``missing`` is empty.
    ``source`` is a path or the file's bytes; ``text`` is the XML itself (xacro's output,
    SPEC-43.3), through the same DOCTYPE and entity refusal; ``label`` names it in
    errors (default: the path's name, else "the URDF"). Raises ``UrdfError`` for anything
    that is not a readable URDF: not XML, a DOCTYPE or entity, no ``<robot>``, a nameless
    or duplicate link or joint, a joint of an unknown type or between links the robot
    does not have, a mimic of a joint it does not have, a number that is not one."""
    if text is not None:
        source = text.encode("utf-8") if isinstance(text, str) else text
    if source is None:
        raise UrdfError("no URDF given (a path, its bytes or text=)")
    if isinstance(source, bytes):
        data = source
        label = label or "the URDF"
        if len(data) > MAX_BYTES:
            raise UrdfError(f"{label} is over {MAX_BYTES // (1024 * 1024)} MB; not a URDF")
    else:
        path = Path(source)
        label = label or path.name
        try:
            if path.stat().st_size > MAX_BYTES:
                raise UrdfError(f"{label} is over {MAX_BYTES // (1024 * 1024)} MB; not a URDF")
            data = path.read_bytes()
        except OSError as e:
            raise UrdfError(f"cannot read {label}: {e.strerror or e}") from None
    root = safe_xml(data, label)
    if root.tag != "robot":
        raise UrdfError(f"{label} is not a URDF (its root element is <{root.tag}>)")
    named: dict[str, tuple[Vec | None, str | None]] = {}
    for m in root.findall("material"):
        mname = (m.get("name") or "").strip()
        if mname:
            named[mname] = _material_parts(m, f"material {mname}")
    links = [_link(el, named) for el in root.findall("link")]
    joints = [_joint(el) for el in root.findall("joint")]
    for kind, items in (("link", links), ("joint", joints)):
        seen: set[str] = set()
        for it in items:
            if it["name"] in seen:
                raise UrdfError(f"two {kind}s are named {it['name']!r}")
            seen.add(it["name"])
    link_names = {lk["name"] for lk in links}
    joint_names = {j["name"] for j in joints}
    children: set[str] = set()
    for j in joints:
        for end in ("parent", "child"):
            if j[end] not in link_names:
                raise UrdfError(f"joint {j['name']}: {end} link {j[end]!r} is not in the robot")
        if j["child"] in children:
            raise UrdfError(f"link {j['child']!r} is the child of two joints")
        children.add(j["child"])
        if j["mimic"] is not None and j["mimic"]["joint"] not in joint_names:
            raise UrdfError(f"joint {j['name']} mimics {j['mimic']['joint']!r}, not in the robot")
    root_link = next((lk["name"] for lk in links if lk["name"] not in children), None)
    return {
        "name": (root.get("name") or "").strip() or Path(label).stem,
        "root": root_link,
        "links": links,
        "joints": joints,
        "missing": [],
    }


# -- files: resolved and served --------------------------------------------------------------


def dae_source(
    path: str, cache_dir: str | Path | None, *, beside: bool = True
) -> tuple[str | None, str | None]:
    """A DAE's GLB for the page, ``(path, None)``, or ``(None, cause)``: SPEC-42's
    ``dae.prepare`` converts it (bounded, in a child process) or finds its current GLB
    (ADR 0028 owns the freshness rule). ``beside`` False (a DAE outside the project)
    keeps the GLB in the cache, never beside its source. Without a cache folder a DAE
    stays missing (``DAE_NOTE``)."""
    if cache_dir is None:
        return None, DAE_NOTE
    from . import dae
    from .scene3d import cache_key

    try:
        scene = dae.prepare(path, cache_dir, cache_key(Path(path)), beside=beside)
    except (dae.DaeError, OSError) as e:
        return None, f"DAE not converted: {e}"
    glb = (scene.get("files") or {}).get("glb")
    return (str(glb), None) if glb else (None, "DAE not converted: no GLB")


class Kind(StrEnum):
    """What a reference is for (``missing[].kind``)."""

    VISUAL = "visual"
    COLLISION = "collision"
    TEXTURE = "texture"


@dataclass
class _Ref:
    """One mesh or texture reference, resolved (``path``) or not (``cause``); a reference
    that is not a ``package://`` one waits for pass two (``deferred``), when the scope is
    known."""

    kind: Kind
    link: str
    uri: str
    target: dict[str, Any]  # the geometry, or the material
    path: str | None = None
    cause: str | None = None
    deferred: bool = False


@dataclass
class _Files:
    resolver: PackageResolver
    cache_dir: str | Path | None = None
    project: list[Path] = field(default_factory=list)
    served: dict[str, str] = field(default_factory=dict)  # role -> path
    missing: list[dict[str, Any]] = field(default_factory=list)
    refs: list[_Ref] = field(default_factory=list)
    _roles: dict[str, str] = field(default_factory=dict)  # path -> role
    _daes: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)

    def take(self, kind: Kind, link: str, uri: str, target: dict[str, Any]) -> None:
        """Pass one: resolve a ``package://`` reference (its package joins the scope),
        defer the others, refuse past ``MAX_REFERENCES``."""
        ref = _Ref(kind, link, uri, target)
        if len(self.refs) >= MAX_REFERENCES:
            ref.cause = f"over {MAX_REFERENCES} mesh and texture references"
        elif uri.strip().lower().startswith("package://"):
            hit = self.resolver.resolve(uri)
            ref.path, ref.cause = hit.path, hit.cause
        else:
            ref.deferred = True
        self.refs.append(ref)

    def scope(self) -> list[Path]:
        """Where a served file may lie: the project and the packages this parse found."""
        return [*self.project, *(folder for folder, _via in self.resolver.packages.values())]

    def finish(self) -> None:
        """Pass two, once every package is known: scope, types, DAEs, roles."""
        scope = self.scope()
        for ref in self.refs:
            if ref.deferred:  # outside the scope before anything asks whether it exists
                hit = self.resolver.resolve(ref.uri, scope)
                ref.path, ref.cause = hit.path, hit.cause
            path, cause = ref.path, ref.cause
            if path is not None and not under(path, scope):
                path, cause = None, OUTSIDE
            if path is not None:
                path, cause = (self._texture if ref.kind == Kind.TEXTURE else self._mesh)(path)
            if path is None:
                self.missing.append(
                    {"link": ref.link, "kind": ref.kind, "uri": ref.uri, "cause": cause}
                )
                continue
            role = self._roles.get(path)
            if role is None:
                role = self._roles[path] = f"m{len(self._roles)}"
                self.served[role] = path
            if ref.kind == Kind.TEXTURE:
                ref.target["texture_file"] = role
            else:
                ref.target["ext"] = MESH_EXTS[Path(path).suffix.lower()]
                ref.target["file"] = role

    def _mesh(self, path: str) -> tuple[str | None, str | None]:
        suffix = Path(path).suffix.lower()
        if suffix == ".dae":
            if path not in self._daes:
                if len(self._daes) >= MAX_DAE_CONVERSIONS:
                    return None, f"over {MAX_DAE_CONVERSIONS} DAE conversions"
                beside = under(path, self.project)
                self._daes[path] = dae_source(path, self.cache_dir, beside=beside)
            path, cause = self._daes[path]
            if path is None:
                return None, cause
        elif suffix not in MESH_EXTS:
            return None, f"mesh format {suffix or '(none)'} is not supported ({SUPPORTED_NOTE})"
        refused = servable(path, MESH_EXTS)
        return (None, f"mesh file {refused}") if refused else (path, None)

    def _texture(self, path: str) -> tuple[str | None, str | None]:
        suffix = Path(path).suffix.lower()
        if suffix not in TEXTURE_EXTS:
            return None, f"texture format {suffix or '(none)'} is not an image"
        refused = servable(path, TEXTURE_EXTS)
        return (None, f"texture file {refused}") if refused else (path, None)


def build_robot(
    path: str | Path,
    *,
    package_paths: Mapping[str, str] | Iterable[str] | None = None,
    search_roots: Iterable[str | Path] = (),
    environ: Mapping[str, str] | None = None,
    text: str | bytes | None = None,
    cache_dir: str | Path | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Parse ``path`` and resolve its files: ``{model, files, packages, scope, actuated,
    parse_ms}`` (module docstring; ``scope``: the folders a served file may lie in). With
    ``text`` (xacro's expanded URDF, SPEC-43.3) the XML is that text, and ``path`` (the
    file it came from) still anchors relative mesh paths and the walk up to the nearest
    package.xml. ``search_roots`` is the project (none: the URDF's own folder).
    ``cache_dir`` is where a DAE's converted GLB may go (none: DAEs stay missing).
    Raises ``UrdfError`` for anything that is not a readable URDF."""
    start = time.perf_counter()
    if progress:
        progress(0.0, "parsing")
    model = parse_urdf(text=text, label=Path(path).name) if text is not None else parse_urdf(path)
    roots = [Path(r) for r in search_roots]
    resolver = PackageResolver(
        path, package_paths=package_paths, search_roots=roots, environ=environ
    )
    project = [r.resolve() for r in roots] or [Path(path).resolve().parent]
    files = _Files(resolver, cache_dir, project)
    for link in model["links"]:
        for kind, shapes in (("visual", link["visuals"]), ("collision", link["collisions"])):
            for shape in shapes:
                geo = shape["geometry"]
                if geo["type"] == "mesh":
                    files.take(Kind(kind), link["name"], geo["uri"], geo)
                mat = shape.get("material")
                if mat is not None and "texture" in mat:
                    files.take(Kind.TEXTURE, link["name"], mat.pop("texture"), mat)
    files.finish()
    model["missing"] = files.missing
    joints = model["joints"]
    if progress:
        progress(1.0, f"{len(model['links'])} links, {len(joints)} joints")
    return {
        "model": model,
        "files": files.served,
        "packages": {
            name: {"path": str(folder), "via": via}
            for name, (folder, via) in resolver.packages.items()
        },
        "scope": [str(p) for p in files.scope()],
        "actuated": [j["name"] for j in joints if j["type"] in ACTUATED and j["mimic"] is None],
        # parsing and resolving, measured (SPEC-43: parse times measured in slice 1)
        "parse_ms": round((time.perf_counter() - start) * 1000.0, 3),
    }


def count_notes(entries: Iterable[Mapping[str, Any]], verb: str, key: str = "cause") -> list[str]:
    """Health notes, one per cause: ``"3 meshes missing: <cause>"`` (``verb``
    ``missing``, the model's ``missing``) or ``"1 mesh unreadable: <error>"`` (``verb``
    ``unreadable``, ``key`` ``error``: the page's ``loaded.failed``). A texture entry
    counts as a texture. Most first, ties in order of first appearance; a package not
    found says how to fix it."""
    counts: dict[tuple[str, str], int] = {}
    for e in entries:
        noun = "texture" if e.get("kind") == "texture" else "mesh"
        cause = str(e.get(key) or ("unknown" if verb == "missing" else "unreadable"))
        counts[(noun, cause)] = counts.get((noun, cause), 0) + 1
    notes = []
    for (noun, cause), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        plural = ("es" if noun == "mesh" else "s") if n != 1 else ""
        line = f"{n} {noun}{plural} {verb}: {cause}"
        if cause.startswith("package `") and cause.endswith("not found"):
            line += f"; {PACKAGE_HINT}"
        notes.append(line)
    return notes


def missing_notes(missing: Iterable[Mapping[str, Any]]) -> list[str]:
    """The notes for the model's ``missing`` (``count_notes``)."""
    return count_notes(missing, "missing")


__all__ = [
    "ACTUATED",
    "CLAMPED",
    "JOINT_TYPES",
    "MAX_DAE_CONVERSIONS",
    "MAX_REFERENCES",
    "MAX_SERVED_BYTES",
    "MESH_EXTS",
    "PACKAGE_NAME",
    "SERVED_EXTS",
    "SIDE_EXTS",
    "Kind",
    "PackageResolver",
    "Resolution",
    "SearchBudget",
    "UrdfError",
    "build_robot",
    "count_notes",
    "dae_source",
    "missing_notes",
    "package_name_of",
    "parse_urdf",
    "safe_xml",
    "servable",
    "under",
]
