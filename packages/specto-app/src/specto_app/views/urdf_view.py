"""The ``urdf`` view host (SPEC-43.1, ADR 0029).

One robot at a time in a Babylon.js page (``web/urdf/index.html``, SPEC-43.2) beside the
3d view's, which does the forward kinematics; this host is the Python half of ADR
0029's host and page contract:

- ``data`` in: the robots its intaker delivered (``.urdf`` items); the picked one (a
  selection's key, else ``params.selected``, else the first) is parsed on the job
  manager (``specto_readers.urdf.build_robot``: the standard library's XML parser plus
  Specto's ``package://`` resolver, with ``params.package_paths`` first), and the footer
  steps through the rest (``hosts.stepping``);
- the meshes are served by the items host as extra roles of the robot's item
  (``specto://items/<link>/<key>/m<n>/<file name>``: the name ends the URL so an OBJ's
  ``.mtl`` and textures resolve next to it), under the link that delivered the robot,
  which the page reads through ``read:link:<id>``;
- Python to page: ``model {generation, model, environments}`` (null model: the file
  could not be read; ``environments``: the 3d view's bundled HDRIs), ``pose {joints,
  source}`` (every actuated joint, ``params.joints`` clamped into the limits, else 0;
  ``source`` is ``params`` or ``zero`` until SPEC-43.3 adds the joint track), ``params``
  (the view's params over the manifest's defaults), and the ``camera`` and ``selection``
  ports (no echo);
- page to Python: ``select {link, joint, add}`` (selection out ``<item key>#<link>``, a
  joint as its child link; content out: the link's or joint's properties),
  ``joints {name: value}`` (a slider release or Reset pose: the full map, which
  replaces ``params.joints`` in one user submit), ``frame {link}`` (the page frames it;
  noted here), ``loaded {ms, meshes, failed}`` (the page's mesh loads) and ``step
  {delta}``; the camera comes as a port emit, and the VIEW panel's and footer's params
  (``selected_links`` among them) as the page's own ``setParams`` writes;
- the host owns the health dot: a mesh not found, or one the page could not read,
  turns it amber (``warn``) with one note per cause; snap lists every one.
"""

from __future__ import annotations

import contextlib
import copy
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from specto_core.contents import ContentEntry, ContentValue
from specto_core.errors import SpectoError
from specto_core.jobs import JobInfo
from specto_core.kinds import Item, PluginManifest, ViewCost, ViewManifest
from specto_core.links import CameraValue, Collection, Selection
from specto_intake.cache import default_cache_root
from specto_readers.urdf import (
    CLAMPED,
    SERVED_EXTS,
    SIDE_EXTS,
    build_robot,
    count_notes,
    missing_notes,
    servable,
    under,
)

from ..hosts.base import HostContext, SimpleFactory
from ..hosts.stepping import Stepping
from ..web.runtime import runtime
from ..web.scheme import item_url
from ..web.webview import WebViewHost
from .scene3d_view import (
    ENV_INTENSITY_MAX,
    ENV_NONE,
    ENVIRONMENTS,
    VENDOR_FILES,
    environment_entries,
)

DATA, CAMERA, SELECTION, CONTENT = "data", "camera", "selection", "content"
PAGE = "urdf/index.html"
JOB_KIND = "urdf.parse"
#: the ROS variables the resolver reads (passed to the job: a pool worker's environment
#: is the app's, but the job says what it used)
ROS_ENV = ("ROS_PACKAGE_PATH", "AMENT_PREFIX_PATH")
HEALTH_SEP = " · "


#: the urdf view type (SPEC-43, ADR 0029): a web view beside the 3d view, its params the
#: 3d view's camera and environment plus the robot's own
URDF_MANIFEST = ViewManifest(
    type="urdf",
    label="URDF",
    host="web",
    doc="One robot (.urdf), read-only; pose it by params.joints. Meshes resolve by "
    "params.package_paths, then package.xml, ROS_PACKAGE_PATH, AMENT_PREFIX_PATH; a "
    "missing one is a placeholder, health warn, in snap.",
    guide="Shows the picked item (or the first; params.selected is its key) and steps "
    "through the rest; never edit the URDF. A click selects a link: <item key>#<link name> "
    "on selection, the link's or joint's properties on content; the camera port is shared "
    "with the 3d view. "
    "camera_mode orbit: left-drag orbits the pivot, right-drag and the wheel dolly toward "
    "it. The environment is one of the 3d view's HDRIs; none keeps the default lights.",
    accepts=["urdf"],
    cost=ViewCost(web_views=1, viewports3d=1),
    defaults={
        "joints": {},
        "view_mode": "lit",
        "up": "z",
        "environment": ENV_NONE,
        "environment_intensity": 1.0,
        "environment_background": False,
        "environment_rotation": 0.0,
        "show_grid": True,
        "show_frames": False,
        "show_joint_axes": False,
        "show_com": False,
        "camera_speed": 1.0,
        "camera_mode": "free",
    },
    state_schema={
        "type": "object",
        "properties": {
            "joints": {
                "type": "object",
                "additionalProperties": {"type": "number"},
                "default": {},
                "description": "{joint: value}, rad or m, clamped to limits.",
            },
            "package_paths": {
                "type": ["object", "array"],
                "additionalProperties": {"type": "string"},
                "items": {"type": "string"},
                "description": "{package: folder} or folders, tried first.",
            },
            "view_mode": {
                "enum": ["lit", "unlit", "wireframe", "collision", "visual_collision"],
                "default": "lit",
            },
            "up": {"enum": ["x", "y", "z", "-x", "-y", "-z"], "default": "z"},
            "environment": {
                "enum": [ENV_NONE, *ENVIRONMENTS],
                "default": ENV_NONE,
            },
            "environment_intensity": {
                "type": "number",
                "x-describe": True,
                "minimum": 0,
                "maximum": ENV_INTENSITY_MAX,
                "default": 1.0,
            },
            "environment_background": {"type": "boolean", "default": False, "x-describe": True},
            "environment_rotation": {
                "type": "number",
                "x-describe": True,
                "minimum": 0,
                "maximum": 360,
                "default": 0.0,
            },
            "show_grid": {"type": "boolean", "default": True, "x-describe": True},
            "show_frames": {"type": "boolean", "default": False, "x-describe": True},
            "show_joint_axes": {"type": "boolean", "default": False, "x-describe": True},
            "show_com": {"type": "boolean", "default": False, "x-describe": True},
            "camera_speed": {
                "type": "number",
                "x-describe": True,
                "minimum": 0.125,
                "maximum": 8,
                "default": 1.0,
            },
            "camera_mode": {
                "enum": ["free", "orbit"],
                "default": "free",
            },
            "selected": {
                "type": ["string", "null"],
                "x-describe": True,
            },
            "selected_links": {
                "type": "array",
                "x-describe": True,
                "items": {"type": "string"},
                "default": [],
                "description": "By name.",
            },
            "panels": {"type": "object", "additionalProperties": True},
        },
        "additionalProperties": True,
    },
    example={
        "view": {"type": "urdf", "label": "Arm", "params": {"joints": {"elbow": 1.2}}},
    },
)
URDF_PLUGIN = PluginManifest(name="app-urdf", version="0.1.0", views=[URDF_MANIFEST])


def urdf_part(item: Item) -> str | None:
    return item.parts.get(".urdf")


@dataclass(frozen=True)
class ServedRobot:
    """What ``RobotMeshes`` serves for one robot."""

    link: str = ""
    key: str | None = None
    #: the item's own parts and the robot's files, by role
    table: Mapping[str, str] = field(default_factory=dict)
    #: every robot file's role (a mesh or a texture) -> its suffix without the dot
    #: (``obj``, ``gltf``, ``png``, ...); the item's own parts are not in it
    files: Mapping[str, str] = field(default_factory=dict)
    #: the folders a served file may lie in: the project and the robot's packages
    scope: tuple[str, ...] = ()


class RobotMeshes:
    """An item resolver serving the shown robot's meshes as extra roles of its item
    (``.m0``, ``.m1``, ...) beside the item's own parts; first in the runtime's chain,
    answering only for that one item of that one link."""

    def __init__(self) -> None:
        self.robot = ServedRobot()

    @property
    def link(self) -> str:
        return self.robot.link

    @property
    def key(self) -> str | None:
        return self.robot.key

    @property
    def table(self) -> Mapping[str, str]:
        return self.robot.table

    @property
    def files(self) -> Mapping[str, str]:
        return self.robot.files

    @property
    def scope(self) -> tuple[str, ...]:
        return self.robot.scope

    def set(self, robot: ServedRobot) -> None:
        self.robot = robot

    def clear(self) -> None:
        self.robot = ServedRobot()

    def _ok(self, path: Path, allowed: frozenset[str]) -> bool:
        """A file may be served now: its resolved path (links followed: a mesh swapped
        for a link after the parse is judged by its target) is a regular file of an
        ``allowed`` type under the cap, inside the project or the robot's packages."""
        try:
            real = path.resolve()
        except OSError:
            return False
        return servable(real, allowed) is None and under(real, self.scope)

    def side_allowed(self, link_id: str, key: str, role: str, path: Path) -> bool:
        """A file named under one of the robot's parts (``specto://items/.../<role>/<name>``):
        the file itself, else only an OBJ's .mtl and images, or a glTF's buffers and
        images; each checked now (``_ok``). Nothing next to the URDF or other meshes."""
        if link_id != self.link or key != self.key or role not in self.files:
            return False
        own = self.table.get(role)
        if own is not None and Path(own).resolve() == path.resolve():
            return self._ok(path, SERVED_EXTS)  # the file by its own name
        allowed = SIDE_EXTS.get(self.files[role])
        return allowed is not None and self._ok(path, allowed)

    # -- ItemResolver ----------------------------------------------------------------------
    def parts(self, link_id: str, key: str) -> dict[str, str] | None:
        """The item's own parts and the robot's files, each robot file checked again
        (``_ok``) so one swapped after the parse is not served unchecked."""
        if not self.table or link_id != self.link or key != self.key:
            return None
        return {
            role: path
            for role, path in self.table.items()
            if role not in self.files or self._ok(Path(path), SERVED_EXTS)
        }

    def keys(self, link_id: str) -> list[str]:
        return [self.key] if self.key and link_id == self.link and self.table else []

    def lookup(self, link_id: str, key: str) -> tuple[RobotMeshes, dict[str, str]] | None:
        p = self.parts(link_id, key)
        return (self, p) if p else None

    def root(self, link_id: str, key: str) -> str | None:
        """No ``@root/`` paths for a robot (ADR 0028's): its meshes often live outside
        the project, and an OBJ's .mtl and images are served next to it instead."""
        return None

    def guard_root(self, link_id: str) -> str | None:
        """The project (the scope's first folder), which a part's own path is judged
        against before anything is stat'ed (SPEC-91): a project on a share serves its own
        robot; package folders on a network path are never resolved in the first place."""
        return self.scope[0] if link_id == self.link and self.scope else None


class UrdfHost(Stepping, WebViewHost):
    page = PAGE
    vendor_files = VENDOR_FILES
    needs_monaco = False

    def __init__(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> None:
        super().__init__(ctx, view_id, manifest)  # a QObject: no attributes before this
        self.collection: Collection | None = None
        self.items: list[Item] = []
        self.keys: list[str] = []
        self.index = -1
        self.path: str | None = None
        self.robot: dict[str, Any] | None = None  # build_robot's result for ``path``
        self.model: dict[str, Any] | None = None  # the model as the page gets it
        self.error: str | None = None
        self.generation = 0
        self.selected_links: list[str] = []
        self.selected_joint: str | None = None
        self.framed: str | None = None
        self.loaded: dict[str, Any] | None = None  # the page's last ``loaded``
        self._wanted_key: str | None = None
        self._wanted_links: list[str] | None = None
        self._package_paths: Any = self.view_params().get("package_paths")
        self._job: str | None = None
        self._meshes = RobotMeshes()
        self._parses = 0  # each parse's token: the newest one's result wins
        runtime().items.add(self._meshes, first=True)
        self.set_health("paused", "no data")

    # -- params --------------------------------------------------------------------------
    def view_params(self) -> dict[str, Any]:
        """The view's params over the manifest's defaults (what the page gets)."""
        try:
            own = dict(self.view.get("params", {}))
        except SpectoError:  # the view left the canvas
            own = {}
        return {**copy.deepcopy(self.manifest.defaults), **own}

    def page_params(self) -> dict[str, Any]:
        out = self.view_params()
        out.pop("package_paths", None)
        out.pop("joints", None)
        return out

    def _submit_params(self, patch: dict[str, Any], what: str) -> None:
        doc = self.ctx.live.document()
        view = doc["views"].get(self.view_id)
        if view is None:
            return
        view.setdefault("params", {}).update(patch)
        self.ctx.submit(doc, f"{view.get('label') or self.view_id}: {what}")

    # -- ports ---------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        self.refresh_grants()
        if port == DATA:
            self.collection = value if isinstance(value, Collection) else None
            self.reload()
        elif port == SELECTION:
            self.page.receive_port(port, value)
            if isinstance(value, Selection):
                self._take_selection(value)
        elif port == CAMERA:
            self.page.receive_port(port, value)

    def _take_selection(self, value: Selection) -> None:
        picked = value.picked
        key = picked.split("#", 1)[0] if picked else None
        links = [k.split("#", 1)[1] for k in value.keys if key and k.startswith(f"{key}#")]
        self._wanted_key, self._wanted_links = key, links
        if key in self.keys and key != self.key:
            self._show(self.keys.index(key))  # applies the wanted links once parsed
        elif key == self.key:
            self._apply_wanted_links()

    # -- items -----------------------------------------------------------------------------
    def reload(self) -> None:
        """(Re)read the collection and show one robot: the one shown, else the one a
        selection or ``params.selected`` names, else the first."""
        shown = self.key
        self.items = [
            it for it in (self.collection.items if self.collection else []) if urdf_part(it)
        ]
        self.keys = [it.key for it in self.items]
        i = -1
        for key in (shown, self._wanted_key, self.view_params().get("selected")):
            if key is not None and str(key) in self.keys:
                i = self.keys.index(str(key))
                break
        if i < 0 and self.items:
            i = 0
        if not self.items:
            self.set_health("paused", "no robots" if self.collection is not None else "no data")
        self._show(i, force=True)

    @property
    def key(self) -> str | None:
        return self.keys[self.index] if 0 <= self.index < len(self.keys) else None

    @property
    def data_link(self) -> str:
        return self.collection.link if self.collection is not None else ""

    def _show(self, index: int, *, force: bool = False) -> None:
        self.index = index if 0 <= index < len(self.items) else -1
        path = urdf_part(self.items[self.index]) if self.index >= 0 else None
        self.sync_step()
        if path == self.path and not force:
            return
        if path != self.path:
            self.selected_links, self.selected_joint = [], None
        self.path = path
        self._parse()

    def _parse(self) -> None:
        """Parse the shown robot on the job manager (a newer parse wins)."""
        self.robot = self.model = None
        self.error = None
        self._meshes.clear()
        if self.path is None:
            self._job = None
            self._send_model()
            return
        self.set_health("loading", f"parsing {Path(self.path).name}")
        params = self.view_params()
        self._parses += 1
        token = f"{self.view_id}:{self._parses}"
        self._job = token
        self.ctx.engine.jobs.submit(
            JOB_KIND,
            build_robot,
            self.path,
            package_paths=self._package_paths_abs(params.get("package_paths")),
            cache_dir=str(default_cache_root()),
            search_roots=[str(r) for r in [self._project_root()] if r],
            environ={k: os.environ[k] for k in ROS_ENV if k in os.environ},
            canvas=self.view_id,
            on_done=lambda job, t=token: self._parsed(t, job),
        )

    def _project_root(self) -> Path | None:
        root = self.ctx.project_root
        if root is None and self.collection is not None and self.collection.root:
            return Path(self.collection.root)
        return root

    def _package_paths_abs(self, value: Any) -> Any:
        """``params.package_paths`` with relative folders under the project."""
        root = self._project_root()

        def absolute(p: Any) -> str:
            path = Path(str(p))
            return str(path if path.is_absolute() or root is None else root / path)

        if isinstance(value, dict):
            return {str(k): absolute(v) for k, v in value.items()}
        if isinstance(value, list):
            return [absolute(v) for v in value]
        return None

    def _parsed(self, token: str, job: JobInfo) -> None:
        if token != self._job or self.disposed:
            return  # a robot the user has stepped past, or a newer parse
        self._job = None
        if job.state != "done" or not isinstance(job.result, dict):
            first = (job.error or job.state).splitlines()[0]
            self.error = first.split(": ", 1)[1] if first.startswith("UrdfError: ") else first
            self._send_model()
            self.set_health("error", self.error)
            return
        self.robot = job.result
        self._serve()
        self._send_model()
        self._send_pose()
        self._apply_wanted_links()
        self._say_health()

    def _serve(self) -> None:
        """Serve the robot's files as roles of its item and put their URLs in the model."""
        assert self.robot is not None
        key = self.key or ""
        files: dict[str, str] = self.robot["files"]
        urls = {
            role: f"{item_url(self.data_link, key, role)}/{quote(Path(p).name, safe='')}"
            for role, p in files.items()
        }
        it = self.items[self.index]
        self._meshes.set(
            ServedRobot(
                link=self.data_link,
                key=key,
                table={**dict(it.parts), **{"." + r: p for r, p in files.items()}},
                files={"." + r: Path(p).suffix.lower().lstrip(".") for r, p in files.items()},
                scope=tuple(self.robot.get("scope") or ()),
            )
        )
        model = copy.deepcopy(self.robot["model"])
        for link in model["links"]:
            for shape in [*link["visuals"], *link["collisions"]]:
                geo = shape.get("geometry") or {}
                role = geo.pop("file", None)
                if geo.get("type") == "mesh":
                    geo["url"] = urls.get(role) if role else None
                mat = shape.get("material")
                if isinstance(mat, dict):
                    tex = mat.pop("texture_file", None)
                    mat["texture_url"] = urls.get(tex) if tex else None
        self.model = model

    def _send_model(self) -> None:
        self.generation += 1
        self.loaded = None
        self._notify_model()

    def _notify_model(self) -> None:
        self.notify(
            "model",
            {
                "generation": self.generation,
                "model": self.model,
                "environments": environment_entries(),
            },
        )

    # -- the pose ------------------------------------------------------------------------------
    def _joint_table(self) -> dict[str, dict[str, Any]]:
        return {j["name"]: j for j in (self.model or {}).get("joints", [])}

    def actuated(self) -> list[str]:
        return list((self.robot or {}).get("actuated", []))

    def clamp(self, name: str, value: float) -> float:
        j = self._joint_table().get(name)
        if j is None or j["type"] not in CLAMPED or j.get("limit") is None:
            return float(value)
        lo, hi = j["limit"]["lower"], j["limit"]["upper"]
        return float(min(max(float(value), lo), hi)) if lo <= hi else float(value)

    def pose(self) -> tuple[dict[str, float], str, list[str]]:
        """``(joints, source, unknown names)``: every actuated joint at its
        ``params.joints`` value clamped into its limits, else 0 (clamped too)."""
        given = self.view_params().get("joints") or {}
        given = given if isinstance(given, dict) else {}
        table = self._joint_table()
        joints: dict[str, float] = {}
        for name in self.actuated():
            raw = given.get(name, 0.0)
            try:
                joints[name] = self.clamp(name, float(raw))
            except (TypeError, ValueError):
                joints[name] = self.clamp(name, 0.0)
        unknown = sorted(n for n in given if n not in table)
        source = "params" if any(n in joints for n in given) else "zero"
        return joints, source, unknown

    def _send_pose(self) -> None:
        if self.robot is None:
            return
        joints, source, _unknown = self.pose()
        self.notify("pose", {"joints": joints, "source": source})

    # -- health --------------------------------------------------------------------------------
    def _say_health(self) -> None:
        if self.robot is None:
            return
        model = self.robot["model"]
        n_links, n_joints = len(model["links"]), len(model["joints"])
        head = (
            f"{model['name']}: {n_links} link{'s' if n_links != 1 else ''}, "
            f"{n_joints} joint{'s' if n_joints != 1 else ''}"
        )
        notes = missing_notes(model["missing"])
        notes.extend(count_notes((self.loaded or {}).get("failed") or [], "unreadable", "error"))
        _joints, _source, unknown = self.pose()
        if unknown:
            n = len(unknown)
            notes.append(
                f"{n} joint name{'s' if n != 1 else ''} not in the robot: {', '.join(unknown)}"
            )
        self.set_health("warn" if notes else "ok", HEALTH_SEP.join([head, *notes]))

    # -- selection and content ---------------------------------------------------------------
    def _apply_wanted_links(self) -> None:
        if self._wanted_links is None or self.robot is None or self._wanted_key != self.key:
            return
        names = {lk["name"] for lk in self.robot["model"]["links"]}
        self.selected_links = [n for n in self._wanted_links if n in names]
        self.selected_joint = None
        self._wanted_links = None
        self._emit_content()

    def select(self, link: str | None, joint: str | None, add: bool) -> Selection:
        """A pick on the page (a mesh or a row): a joint selects its child link."""
        if joint is not None:
            j = self._joint_table().get(joint)
            link = j["child"] if j else link
            joint = joint if j else None
        if link is None:
            self.selected_links, self.selected_joint = [], None
        else:
            links = [n for n in self.selected_links if n != link] if add else []
            self.selected_links = [*links, link]
            self.selected_joint = joint
        return self._emit_links()

    def _emit_links(self) -> Selection:
        """The selected links out on ``selection`` (``<item key>#<link>``) and the
        last one's (or the joint's) properties on ``content``."""
        key = self.key
        keys = [f"{key}#{n}" for n in self.selected_links] if key else []
        sel = Selection(keys=keys, last=keys[-1] if keys else None)
        self.emit(SELECTION, sel)
        self._emit_content()
        return sel

    def properties(self) -> dict[str, Any] | None:
        """The selected joint's, else the last selected link's properties."""
        if self.model is None:
            return None
        if self.selected_joint is not None:
            j = self._joint_table().get(self.selected_joint)
            if j is not None:
                return {"kind": "joint", **copy.deepcopy(j)}
        if not self.selected_links:
            return None
        name = self.selected_links[-1]
        link = next((lk for lk in self.model["links"] if lk["name"] == name), None)
        if link is None:
            return None
        parent = next((j["name"] for j in self.model["joints"] if j["child"] == name), None)
        return {"kind": "link", **copy.deepcopy(link), "parent_joint": parent}

    def _emit_content(self) -> None:
        root = str(Path(self.path).parent) if self.path else ""
        props = self.properties()
        if props is None:
            self.emit(CONTENT, ContentValue(root=root))
            return
        name = props["name"] if props["kind"] == "link" else props["child"]
        entry = ContentEntry(
            key=f"{self.key}#{name}",
            path=f"{Path(self.path or '').name}#{name}",
            format="json",
            data=props,
        )
        self.emit(CONTENT, ContentValue(root=root, entries=[entry]))

    # -- stepping (hosts.stepping) -------------------------------------------------------------
    def step_keys(self) -> list[str]:
        return list(self.keys)

    def step_key(self) -> str | None:
        return self.key

    def show_step(self, key: str) -> bool:
        if key not in self.keys:
            return False
        self._wanted_key = key
        self._show(self.keys.index(key))
        return True

    @property
    def emits_selection(self) -> bool:
        return False  # the selection port carries links, not the robot stepped to

    def on_step_state(self, state: dict[str, int]) -> None:
        self.notify("step", state)

    # -- the page -------------------------------------------------------------------------------
    def on_ready(self) -> None:
        self._notify_model()
        self.notify("params", self.page_params())
        self._send_pose()
        self.sync_step()

    def on_params_changed(self, params: dict[str, Any]) -> None:
        self.notify("params", self.page_params())
        package_paths = params.get("package_paths")
        if package_paths != self._package_paths:
            self._package_paths = package_paths
            if self.path is not None:
                self._parse()
                return
        self._send_pose()
        self._take_selected_links(params.get("selected_links"))
        self._say_health()

    def _take_selected_links(self, value: Any) -> None:
        """``params.selected_links`` changed (the page's write, or the agent's): select
        those links, if that is not the selection already (no echo of a pick)."""
        if not isinstance(value, list) or self.robot is None:
            return
        names = {lk["name"] for lk in self.robot["model"]["links"]}
        links = [str(n) for n in value if str(n) in names]
        if links == self.selected_links:
            return
        self.selected_links, self.selected_joint = links, None
        self._emit_links()

    def on_page_event(self, kind: str, payload: Any) -> None:
        args = payload if isinstance(payload, dict) else {}
        if kind == "select":
            link, joint = args.get("link"), args.get("joint")
            self.select(
                str(link) if link else None, str(joint) if joint else None, bool(args.get("add"))
            )
        elif kind == "joints":
            self.set_joints(args)
        elif kind == "loaded":
            self.on_loaded(args)
        elif kind == "frame":
            link = args.get("link")
            self.framed = str(link) if link else None
        elif kind == "step":
            with contextlib.suppress(TypeError, ValueError):
                self.step_item(int(args.get("delta") or 0))

    def set_joints(self, values: dict[str, Any]) -> None:
        """A slider release or Reset pose: the page's full map of the joints, clamped,
        replaces ``params.joints`` as one user submit. Only actuated, non-mimic joints
        are kept (a mimic follows its master); values that are not numbers are dropped;
        an unchanged map is no submit."""
        actuated = set(self.actuated())
        joints: dict[str, float] = {}
        for name, raw in values.items():
            if name not in actuated:
                continue
            try:
                joints[name] = self.clamp(name, float(raw))
            except (TypeError, ValueError):
                continue
        if joints != (self.view_params().get("joints") or {}):
            self._submit_params({"joints": joints}, "joints")

    def on_loaded(self, args: dict[str, Any]) -> None:
        """The page loaded the model's meshes: ``{ms, meshes, failed: [{link, kind, url,
        error}]}``; the failures join the health notes (the host owns the dot)."""
        failed = args.get("failed") if isinstance(args.get("failed"), list) else []
        self.loaded = {
            "ms": args.get("ms"),
            "meshes": args.get("meshes"),
            "failed": [f for f in failed if isinstance(f, dict)],
        }
        self._say_health()

    # -- lifecycle -------------------------------------------------------------------------------
    def pause(self) -> None:
        super().pause()
        self.notify("render", {"running": False})

    def resume(self) -> None:
        super().resume()
        self.notify("render", {"running": True})

    def snapshot(self) -> dict[str, Any]:
        camera = self.ctx.links.output_of(self.view_id, CAMERA)
        params = self.view_params()
        model = (self.robot or {}).get("model") or {}
        joints, source, unknown = self.pose() if self.robot else ({}, "zero", [])
        return {
            **super().snapshot(),
            "item": self.key,
            "count": len(self.keys),
            "file": self.path,
            "robot": model.get("name"),
            "error": self.error,
            "links": len(model.get("links", [])),
            "joints": len(model.get("joints", [])),
            "actuated": self.actuated(),
            "pose": joints,
            "pose_source": source,
            "unknown_joints": unknown,
            "selected": {"links": list(self.selected_links), "joint": self.selected_joint},
            "framed": self.framed,
            "loaded": self.loaded,
            "view_mode": params.get("view_mode"),
            "environment": params.get("environment"),
            "missing": list(model.get("missing", [])),
            "packages": dict((self.robot or {}).get("packages", {})),
            "parse_ms": (self.robot or {}).get("parse_ms"),
            "camera": camera.model_dump(mode="json") if isinstance(camera, CameraValue) else None,
        }

    def dispose(self) -> None:
        self._job = None
        self._meshes.clear()
        runtime().items.remove(self._meshes)
        super().dispose()


def factory() -> SimpleFactory:
    return SimpleFactory("urdf", UrdfHost, plugin=URDF_PLUGIN)


def register(registry: Any) -> None:
    """``specto.view_hosts`` entry point."""
    registry.add_factory(factory())


__all__ = [
    "URDF_MANIFEST",
    "URDF_PLUGIN",
    "RobotMeshes",
    "ServedRobot",
    "UrdfHost",
    "factory",
    "register",
    "urdf_part",
]
