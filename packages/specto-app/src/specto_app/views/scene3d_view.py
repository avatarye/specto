"""The ``3d`` view host (spec #35 stories 46-47; research #31; ADRs 0014, 0016).

One Babylon.js page (``web/3d/index.html``, WebGL2, vendored offline) shows every 3D
item of the collection on its ``data`` input in one scene: point clouds from the
buffers resolve decoded (``specto_readers.scene3d``), PLY meshes and DAEs as the GLB
resolve wrote (a DAE's beside it, ``specto_readers.dae``), glTF/GLB, STL, OBJ and
Gaussian splats through Babylon's own loaders. The host is glue on
``WebViewHost``:

- ``data`` in: the page is told what to load (``scene``); the files are served by the
  items host under the link that delivered the collection
  (``specto://items/<link>/<key>/<role>``, the derived ``points``/``glb`` roles beside
  the item's own part), which the page may read through ``read:link:<id>``;
- ``camera`` out: the page emits its camera (throttled); ``camera`` in reaches the page
  as a port value and is not echoed;
- ``selection`` out: a click on a point cloud sends a pick ray (``Specto.send("pick")``);
  the host answers from the kd-tree built at resolve (``pick_ray``) and emits
  ``<item key>#<point index>``; a mesh or splat the page picked itself (``select``)
  becomes ``<item key>``; ``selection`` in highlights on the page and is not echoed;
- pause/resume stop and restart the page's render loop (research: Chromium keeps at
  most 16 live WebGL contexts per renderer);
- the scene payload carries the page's first camera (``initial_camera``): it targets
  the world origin, far enough back for everything the entries' bounds reach from it;
  F (frame all) stays the page's own, around the scene's centre;
- and the bundled HDRI environments (#175, ``environments``: name, label, look, author,
  page and the vendored file's URL on the page's own host, null until build.py has
  fetched it); the view params ``environment`` / ``environment_intensity`` /
  ``environment_background`` / ``environment_rotation`` pick and set one.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np

import specto_web
from specto_core.kinds import PluginManifest, ViewCost, ViewManifest
from specto_core.links import CameraValue, Collection, Selection
from specto_readers.kdtree import KdTree
from specto_readers.scene3d import load_points

from ..hosts.base import HostContext, SimpleFactory
from ..web.scheme import ROOT_ANCHOR, item_url
from ..web.webview import WebViewHost
from .file_tree import format_bytes

DATA, CAMERA, SELECTION = "data", "camera", "selection"
#: parts the page fetches by their own file name, so the files they name (a glTF's .bin
#: and images, an OBJ's .mtl and its textures) resolve next to them (SPEC-42)
SIBLING_EXTS = (".gltf", ".obj")
PAGE = "3d/index.html"
VENDOR_FILES = ("babylon/babylon.js", "babylon/babylonjs.loaders.min.js", "babylon/fflate.js")
#: ``camera_speed`` scales flying, zooming and panning (the VIEW panel's slider, #129);
#: ``panels`` (not a default) keeps the side panels' {collapsed, width} by name;
#: ``view_mode`` is one of VIEW_MODES (the footer's mode pill, #153; the usd view's ids);
#: ``environment`` is ``none`` or a bundled HDRI's name (ENVIRONMENTS, #175), lit by
#: ``environment_intensity``, turned ``environment_rotation`` degrees about up and drawn
#: behind the scene when ``environment_background``
VIEW_MODES = ("lit", "unlit", "wireframe", "collision")
ENVIRONMENTS: tuple[str, ...] = tuple(e["name"] for e in specto_web.environments())
ENV_NONE = "none"
ENV_INTENSITY_MAX = 4.0
DEFAULT_PARAMS: dict[str, Any] = {
    "point_size": 2,
    "up": "y",
    "camera_speed": 1.0,
    "view_mode": "lit",
    "environment": ENV_NONE,
    "environment_intensity": 1.0,
    "environment_background": False,
    "environment_rotation": 0.0,
    "mesh_unit": "m",
}
#: what one unit of an STL or OBJ file is, in metres (``mesh_unit``; neither format says)
MESH_UNITS: dict[str, float] = {"m": 1.0, "cm": 0.01, "mm": 0.001, "in": 0.0254}
#: the entries ``mesh_unit`` scales: the formats without units
UNITLESS_EXTS = (".stl", ".obj")
STATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "point_size": {
            "type": "number",
            "minimum": 1,
            "default": 2,
            "description": "Point cloud point size in pixels.",
        },
        "up": {
            "enum": ["y", "z"],
            "default": "y",
            "description": "z stands Z-up data (LiDAR, robot meshes, points, splats) "
            "upright; glTF and DAE keep their own Y up.",
        },
        "camera_speed": {
            "type": "number",
            "minimum": 0.125,
            "maximum": 8,
            "default": 1.0,
            "description": "Scales flying, walking, panning and dollying.",
        },
        "view_mode": {
            "enum": list(VIEW_MODES),
            "default": "lit",
            "description": "collision shows an item's Unreal collision meshes.",
        },
        "environment": {
            "enum": [ENV_NONE, *ENVIRONMENTS],
            "default": ENV_NONE,
            "description": "A bundled HDRI lighting PBR meshes; none: the two default lights.",
        },
        "environment_intensity": {
            "type": "number",
            "minimum": 0,
            "maximum": ENV_INTENSITY_MAX,
            "default": 1.0,
            "description": "How strongly the environment lights the scene.",
        },
        "environment_background": {
            "type": "boolean",
            "default": False,
            "description": "Draw the environment behind the scene (not in wireframe).",
        },
        "environment_rotation": {
            "type": "number",
            "minimum": 0,
            "maximum": 360,
            "default": 0.0,
            "description": "Turns the environment about up, in degrees.",
        },
        "mesh_unit": {
            "enum": list(MESH_UNITS),
            "default": "m",
            "description": "What one STL or OBJ unit is (robot STLs are often mm).",
        },
        "panels": {
            "type": "object",
            "description": "The VIEW and ITEMS side panels' {collapsed, width} by name.",
        },
    },
}
DEFAULT_FOV_Y = 0.8  # Babylon's ArcRotateCamera default (radians)
FIRST_LOOK = (-0.55, -0.6, 0.58)  # the first look: down from a three-quarter view
DEFAULT_RADIUS = 10.0  # the world's reach when nothing has bounds (fly speed agrees)

SCENE3D_MANIFEST = ViewManifest(
    type="3d",
    label="3D",
    host="web",
    doc="Babylon.js scene of every 3D item on its data input: point clouds (up to about "
    "20 M points), glTF/GLB, PLY, STL and OBJ meshes (an OBJ's MTL and textures beside "
    "it), COLLADA .dae (converted to .dae.glb beside it) and Gaussian splats (up to about "
    "2 M). Click a point or a mesh to select it; outputs camera and selection.",
    guide="Unreal Editor viewport controls: drag turns and walks, right-drag looks (W A S "
    "D Q E fly, the wheel sets the speed), middle-drag pans, the wheel dollies, Alt+drag "
    "orbits the selection, F frames the selection or the scene; camera_speed (1/8 to 8) "
    "scales flying, walking, panning and dollying. Points are decoded at resolve. "
    "mesh_unit is what one unit of an STL or OBJ file is (neither format says; robot "
    "STLs are often millimetres); other formats carry their own scale. up: z stands Z-up "
    "data upright (most LiDAR, photogrammetry and robot meshes: STL, OBJ, PLY, points, "
    "splats); glTF/GLB (Y up by its spec) and DAE (converted to Y up from its up_axis, "
    "its unit applied) keep their own and are not turned. view_mode: lit, unlit, "
    "wireframe, or collision when an item carries Unreal collision meshes (UCX_/UBX_/USP_/"
    "UCP_ names). environment: none (two default lights) or a bundled Poly Haven HDRI "
    "(CC0) lighting glTF/PLY meshes' PBR materials: studio (softbox product look), "
    "overcast and sunny (outdoor), indoor (a room), urban (a city square); points and "
    "splats draw as they are; environment_intensity scales it, environment_rotation "
    "turns it about up in degrees, environment_background draws it behind the scene "
    "(not in wireframe).",
    accepts=["pointcloud", "mesh", "splat"],
    cost=ViewCost(web_views=1, viewports3d=1),
    defaults=dict(DEFAULT_PARAMS),
    state_schema=STATE_SCHEMA,
    example={
        "view": {"type": "3d", "label": "Scans", "params": {"point_size": 2, "up": "z"}},
        "link": {
            "kind": "intaker",
            "scope": "pinned",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "kobi", "port": "data"},
            "params": {"sources": ["scans"], "screener": ["@3d"]},
            "summary": {"sentence": "The 3D view shows every scan under scans/."},
        },
    },
)
SCENE3D_PLUGIN = PluginManifest(name="app-3d", version="0.1.0", views=[SCENE3D_MANIFEST])


def initial_camera(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The page's first camera (a ``CameraValue`` as JSON): it targets the world origin
    from ``FIRST_LOOK``, far enough back to hold everything the entries' bounds reach
    from the origin (the frame-all distance, measured from (0, 0, 0) instead of the
    scene's centre)."""
    radius = 0.0
    for e in entries:
        b = e.get("bounds")
        if not b:
            continue
        lo, hi = b
        for x in (lo[0], hi[0]):
            for y in (lo[1], hi[1]):
                for z in (lo[2], hi[2]):
                    radius = max(radius, math.sqrt(x * x + y * y + z * z))
    radius = radius or DEFAULT_RADIUS
    distance = radius / math.sin(DEFAULT_FOV_Y / 2) * 1.05
    n = math.sqrt(sum(c * c for c in FIRST_LOOK))
    return {
        "position": [-c / n * distance for c in FIRST_LOOK],
        "target": [0.0, 0.0, 0.0],
        "up": [0.0, 1.0, 0.0],
        "fov_y": DEFAULT_FOV_Y,
        "near": max(0.001, radius / 5000),
        "far": max(100.0, radius * 50),
    }


def environment_entries() -> list[dict[str, Any]]:
    """The bundled HDRIs as the page lists them: the manifest's name, label, look, author
    and page, and ``url``, the vendored file on the page's own host (``/vendor/hdri/...``,
    a prefiltered .env or the raw .hdr), or None while build.py has not fetched it."""
    out = []
    for e in specto_web.environments():
        f = specto_web.environment_file(e["name"])
        out.append(
            {
                "name": e["name"],
                "label": e.get("label", e["name"]),
                "look": e.get("look", ""),
                "author": e.get("author", ""),
                "page": e.get("page", ""),
                "url": f"/{specto_web.HDRI_DIR}/{f.name}" if f else None,
            }
        )
    return out


def sibling_path(part: str, root: str) -> str:
    """The URL tail a part is fetched by so the files it names resolve beside it: under
    the link's root when the part is inside it (``@root/robot/meshes/panel.obj``: a
    ``../textures/skin.png`` may then leave the OBJ's folder, not the project, ADR 0028),
    else its own name."""
    if root:
        try:
            rel = Path(os.path.relpath(part, root))
        except ValueError:  # another drive
            rel = None
        if rel is not None and rel.parts and ".." not in rel.parts and not rel.is_absolute():
            return "/".join([ROOT_ANCHOR, *(quote(seg, safe="") for seg in rel.parts)])
    return quote(Path(part).name, safe="")


def _file_size(path: str | None) -> int | None:
    if not path:
        return None
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def derived_files(scene: dict[str, Any]) -> dict[str, str]:
    """The files resolve derived for an item (``points`` and ``kdtree``, or ``glb``)."""
    return {str(k): str(v) for k, v in (scene.get("files") or {}).items()}


def scene_entry(
    key: str, index: int, parts: dict[str, str], scene: dict[str, Any], link: str, root: str
) -> dict[str, Any]:
    """The page's entry for one item of the collection under ``link`` (its ``key``, position,
    ``parts`` and ``meta["scene"]``, the link's ``root``): what to load from the items host
    (a derived buffer or GLB, else the file by its own name so a .gltf's .bin or an OBJ's
    MTL resolve beside it) and ``ext`` for Babylon's loader choice; size for the items panel;
    count and bounds; the files an OBJ names that cannot be served; and the up axis the file
    declares, when it does (``specto_readers.scene3d`` says which formats do)."""
    role = str(scene.get("role", ""))
    kind = scene.get("kind")
    files = derived_files(scene)
    entry: dict[str, Any] = {
        "id": key,
        "index": index,
        "kind": kind,
        "label": Path(parts[role]).name if role in parts else key,
    }
    size = _file_size(parts.get(role))
    if size is not None:
        # the items panel shows it under the name (#129)
        entry["size"] = size
        entry["size_label"] = format_bytes(size)
    if kind == "pointcloud":
        entry["url"] = item_url(link, key, "points")
        entry["count"] = scene.get("count")
        entry["bounds"] = scene.get("bounds")
    elif kind == "mesh" and "glb" in files:
        entry["url"] = item_url(link, key, "glb")
        if scene.get("version"):
            # a converted DAE keeps its GLB's path: the version makes a new URL, so an
            # open view loads the GLB again when the DAE changes
            entry["url"] += "?v=" + quote(str(scene["version"]), safe="")
        entry["ext"] = ".glb"
        entry["count"] = scene.get("count")
        entry["bounds"] = scene.get("bounds")
    elif kind == "error":
        entry["error"] = scene.get("error", "unreadable")
    else:
        # Babylon picks its loader by ``ext``; a .gltf or .obj is fetched by its own name
        # so its sibling .bin / .mtl and textures resolve next to it
        url = item_url(link, key, role)
        if role.lower() in SIBLING_EXTS:
            url += "/" + sibling_path(parts[role], root)
        entry["url"] = url
        entry["ext"] = role.lower()
        if scene.get("bounds"):  # STL and OBJ: measured at resolve
            entry["count"] = scene.get("count")
            entry["bounds"] = scene.get("bounds")
        if scene.get("missing"):  # files an OBJ names that cannot be served
            entry["missing"] = list(scene["missing"])
    if scene.get("up") in ("y", "z"):
        # the file declares its up axis: the view's up param leaves it alone
        entry["up"] = scene["up"]
    return entry


class Scene3DHost(WebViewHost):
    page = PAGE
    vendor_files = VENDOR_FILES

    def __init__(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> None:
        super().__init__(ctx, view_id, manifest)  # a QObject: no attributes before this
        self.collection: Collection | None = None
        self.entries: list[dict[str, Any]] = []
        self.selection: Selection | None = None
        self.picked: dict[str, Any] | None = None
        self._files: dict[str, dict[str, str]] = {}  # item key -> derived files
        self._trees: dict[str, KdTree] = {}
        self.set_health("paused", "no data")

    # -- what the page loads ---------------------------------------------------------------------
    def view_params(self) -> dict[str, Any]:
        return {**DEFAULT_PARAMS, **self.page.params}

    def mesh_scale(self) -> float:
        """Metres per STL or OBJ unit, from ``mesh_unit`` (the page scales by it)."""
        return MESH_UNITS.get(str(self.view_params().get("mesh_unit")), 1.0)

    def scene_payload(self) -> dict[str, Any]:
        params = self.view_params()
        unit = self.mesh_scale()
        entries = []
        for e in self.entries:
            e = dict(e)
            if unit != 1.0 and e.get("ext") in UNITLESS_EXTS and e.get("bounds"):
                e["bounds"] = [[x * unit for x in corner] for corner in e["bounds"]]
            entries.append(e)
        return {
            "entries": entries,
            "params": params,
            "generation": self.collection.generation if self.collection else 0,
            "camera": initial_camera(entries),
            "mesh_scale": unit,
            "environments": environment_entries(),
        }

    def _rebuild(self) -> None:
        self.entries = []
        self._files = {}
        self._trees.clear()  # the pick data of the old collection goes with it
        col = self.collection
        link = col.link if col else ""
        root = col.root if col else ""
        for i, it in enumerate(col.items if col else []):
            scene = it.meta.get("scene")
            if not isinstance(scene, dict):
                continue
            self._files[it.key] = derived_files(scene)
            self.entries.append(scene_entry(it.key, i, it.parts, scene, link, root))
        if self.picked is not None and self.picked.get("item") not in self._files:
            self.picked = None  # the picked item left with its collection
        self.notify("scene", self.scene_payload())

    # -- ports ---------------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        if port == DATA:
            self.refresh_grants()
            self.collection = value if isinstance(value, Collection) else None
            self._rebuild()
            if self.collection is None:
                self.set_health("paused", "no data")
            else:
                n = len(self.entries)
                said = f"{n} item{'s' if n != 1 else ''}"
                missing = [
                    f"{e['label']}: {', '.join(e['missing'])} not found or outside the project"
                    for e in self.entries
                    if e.get("missing")
                ]
                self.set_health("ok", " · ".join([said, *missing]))
            return
        if port == SELECTION:
            self.selection = value if isinstance(value, Selection) else None
        super().receive(port, value)

    # -- the page ------------------------------------------------------------------------------
    def on_ready(self) -> None:
        self.notify("scene", self.scene_payload())

    def on_params_changed(self, params: dict[str, Any]) -> None:
        # the unit table lives here; the page only scales by the factor
        self.notify("mesh_scale", {"scale": self.mesh_scale()})
        super().on_params_changed(params)

    def on_page_event(self, kind: str, payload: Any) -> None:
        """``pick`` (a ray from a click on a point cloud) and ``select`` (a mesh or splat
        the page picked itself); both answer with ``picked``."""
        args = payload if isinstance(payload, dict) else {}
        if kind == "pick":
            self.pick_ray(
                args.get("origin") or [0, 0, 0],
                args.get("direction") or [0, 0, -1],
                tolerance=float(args.get("tolerance", 0.005)),
                t_max=float(args.get("t_max", np.inf)),
            )
        elif kind == "select":
            self.select_item(str(args.get("item", "")), args.get("position"))

    def pick_ray(
        self,
        origin: list[float],
        direction: list[float],
        *,
        tolerance: float,
        t_max: float = np.inf,
    ) -> dict[str, Any] | None:
        """The nearest point of any point cloud inside the pick cone; emits the selection
        ``<item key>#<index>`` and tells the page (``picked``, None for a miss)."""
        best: tuple[float, dict[str, Any]] | None = None
        for entry in self.entries:
            if entry.get("kind") != "pointcloud":
                continue
            tree = self._tree(entry)
            if tree is None:
                continue
            hit = tree.pick_ray(origin, direction, tolerance=tolerance, t_max=t_max)
            if hit is None:
                continue
            index, t, _perp = hit
            if best is None or t < best[0]:
                pos = [float(x) for x in tree.points[index]]
                best = (t, {"item": entry["id"], "index": index, "position": pos, "distance": t})
        if best is None:
            self.notify("picked", None)
            return None
        self.picked = best[1]
        key = f"{best[1]['item']}#{best[1]['index']}"
        self.emit(SELECTION, Selection(keys=[key], last=key))
        self.notify("picked", self.picked)
        return self.picked

    def select_item(self, key: str, position: Any = None) -> dict[str, Any]:
        """A mesh or splat the page picked (Babylon's own picking): the whole item."""
        pos = [float(x) for x in position] if isinstance(position, list | tuple) else None
        self.picked = {"item": key, "index": None, "position": pos}
        self.emit(SELECTION, Selection(keys=[key], last=key))
        self.notify("picked", self.picked)
        return self.picked

    def _tree(self, entry: dict[str, Any]) -> KdTree | None:
        tree = self._trees.get(entry["id"])
        if tree is not None:
            return tree
        files = self._files.get(entry["id"], {})
        if "points" not in files or "kdtree" not in files:
            return None
        try:
            xyz, _rgba = load_points(files["points"], int(entry["count"]))
            tree = KdTree.load(files["kdtree"], xyz)
        except (OSError, ValueError) as e:
            self.set_health("error", f"pick: {e}")
            return None
        self._trees[entry["id"]] = tree
        return tree

    # -- lifecycle ---------------------------------------------------------------------------------
    def pause(self) -> None:
        super().pause()
        self.notify("render", {"running": False})

    def resume(self) -> None:
        super().resume()
        self.notify("render", {"running": True})

    def snapshot(self) -> dict[str, Any]:
        camera = self.ctx.links.output_of(self.view_id, CAMERA)
        points = sum(
            int(e.get("count") or 0) for e in self.entries if e.get("kind") == "pointcloud"
        )
        kinds = ("pointcloud", "mesh", "splat", "error")
        return {
            **super().snapshot(),
            "entries": len(self.entries),
            "kinds": {k: sum(1 for e in self.entries if e.get("kind") == k) for k in kinds},
            "points": points,
            "camera": camera.model_dump(mode="json") if isinstance(camera, CameraValue) else None,
            "selection": list(self.selection.keys) if self.selection else [],
            "picked": self.picked,
            "environment": self.view_params()["environment"],
            "missing": {e["id"]: e["missing"] for e in self.entries if e.get("missing")},
        }

    def dispose(self) -> None:
        self._trees.clear()
        super().dispose()


def factory() -> SimpleFactory:
    return SimpleFactory("3d", Scene3DHost, plugin=SCENE3D_PLUGIN)


def register(registry: Any) -> None:
    """``specto.view_hosts`` entry point."""
    registry.add_factory(factory())


__all__ = [
    "ENVIRONMENTS",
    "SCENE3D_MANIFEST",
    "SCENE3D_PLUGIN",
    "Scene3DHost",
    "environment_entries",
    "factory",
    "register",
]
