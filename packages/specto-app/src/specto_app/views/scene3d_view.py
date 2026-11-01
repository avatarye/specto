"""The ``3d`` view host (spec #35 stories 46-47; research #31; ADRs 0014, 0016).

One Babylon.js page (``web/3d/index.html``, WebGL2, vendored offline) shows every 3D
item of the collection on its ``data`` input in one scene: point clouds from the
buffers resolve decoded (``specto_readers.scene3d``), PLY meshes as the GLB resolve
wrote, glTF/GLB and Gaussian splats through Babylon's own loaders. The host is glue on
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
from pathlib import Path
from typing import Any

import numpy as np

import specto_web
from specto_core.kinds import PluginManifest, ViewCost, ViewManifest
from specto_core.links import CameraValue, Collection, Selection
from specto_readers.kdtree import KdTree
from specto_readers.scene3d import load_points

from ..hosts.base import HostContext, SimpleFactory
from ..web.scheme import item_url
from ..web.webview import WebViewHost
from .file_tree import format_bytes

DATA, CAMERA, SELECTION = "data", "camera", "selection"
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
}
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
            "description": "The data's up axis: z for most LiDAR and photogrammetry.",
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
            "description": "lit, unlit, wireframe, or collision when an item carries Unreal "
            "collision meshes (UCX_/UBX_/USP_/UCP_ names).",
        },
        "environment": {
            "enum": [ENV_NONE, *ENVIRONMENTS],
            "default": ENV_NONE,
            "description": "Image-based lighting from a bundled HDRI (Poly Haven, CC0): "
            "studio (softbox product look), overcast and sunny (outdoor), indoor (a room), "
            "urban (a city square); it lights glTF/PLY meshes' PBR materials in place of "
            "the two default lights. none: the default lights. Points and splats draw "
            "as they are.",
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
            "description": "Turns the environment about the up axis, in degrees.",
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
    doc="Babylon.js scene of every 3D item on its data input: point clouds (decoded at "
    "resolve, up to about 20 M points), glTF/GLB and PLY meshes, Gaussian splats (up to "
    "about 2 M). Unreal Editor viewport controls: drag turns and walks, right-drag looks "
    "(W A S D Q E fly, the wheel sets the speed), middle-drag pans, the wheel dollies, "
    "Alt+drag orbits the selection, F frames the selection or the scene; click a point "
    "or a mesh to select it. camera_speed (1 = default, 1/8 to 8) scales flying, "
    "walking, panning and dollying. view_mode: lit (default), unlit, wireframe, or "
    "collision when an item carries Unreal collision meshes (UCX_/UBX_/USP_/UCP_ names). "
    "environment: none (two default lights) or a bundled HDRI lighting PBR meshes "
    "(studio, overcast, sunny, indoor, urban), with environment_intensity, "
    "environment_rotation (degrees) and environment_background (draw it behind). "
    "Outputs camera and selection.",
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


def _file_size(path: str | None) -> int | None:
    if not path:
        return None
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


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

    def scene_payload(self) -> dict[str, Any]:
        return {
            "entries": [dict(e) for e in self.entries],
            "params": self.view_params(),
            "generation": self.collection.generation if self.collection else 0,
            "camera": initial_camera(self.entries),
            "environments": environment_entries(),
        }

    def _rebuild(self) -> None:
        self.entries = []
        self._files = {}
        self._trees.clear()  # the pick data of the old collection goes with it
        col = self.collection
        link = col.link if col else ""
        for i, it in enumerate(col.items if col else []):
            scene = it.meta.get("scene")
            if not isinstance(scene, dict):
                continue
            role = str(scene.get("role", ""))
            kind = scene.get("kind")
            files = {str(k): str(v) for k, v in (scene.get("files") or {}).items()}
            self._files[it.key] = files
            entry: dict[str, Any] = {
                "id": it.key,
                "index": i,
                "kind": kind,
                "label": Path(it.parts[role]).name if role in it.parts else it.key,
            }
            size = _file_size(it.parts.get(role))
            if size is not None:
                # the items panel shows it under the name (#129)
                entry["size"] = size
                entry["size_label"] = format_bytes(size)
            if kind == "pointcloud":
                entry["url"] = item_url(link, it.key, "points")
                entry["count"] = scene.get("count")
                entry["bounds"] = scene.get("bounds")
            elif kind == "mesh" and "glb" in files:
                entry["url"] = item_url(link, it.key, "glb")
                entry["ext"] = ".glb"
                entry["count"] = scene.get("count")
                entry["bounds"] = scene.get("bounds")
            elif kind == "error":
                entry["error"] = scene.get("error", "unreadable")
            else:
                # Babylon picks its loader by ``ext``; a .gltf is fetched by its own
                # name so its sibling .bin and textures resolve next to it
                url = item_url(link, it.key, role)
                if role.lower() == ".gltf":
                    url += "/" + Path(it.parts[role]).name
                entry["url"] = url
                entry["ext"] = role.lower()
            self.entries.append(entry)
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
                self.set_health("ok", f"{n} item{'s' if n != 1 else ''}")
            return
        if port == SELECTION:
            self.selection = value if isinstance(value, Selection) else None
        super().receive(port, value)

    # -- the page ------------------------------------------------------------------------------
    def on_ready(self) -> None:
        self.notify("scene", self.scene_payload())

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
