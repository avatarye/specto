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
from pathlib import Path
from typing import Any
from urllib.parse import quote

from specto_core.contents import ContentEntry, ContentValue
from specto_core.errors import SpectoError
from specto_core.jobs import JobInfo
from specto_core.kinds import Item, PluginManifest, ViewCost, ViewManifest
from specto_core.links import CameraValue, Collection, Selection
from specto_readers.urdf import build_robot, missing_notes

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

DATA, JOINTS, TIME, CAMERA, SELECTION, CONTENT = (
    "data",
    "joints",
    "time",
    "camera",
    "selection",
    "content",
)
PAGE = "urdf/index.html"
URDF_ROLES = (".urdf",)
JOB_KIND = "urdf.parse"
#: the ROS variables the resolver reads (passed to the job: a pool worker's environment
#: is the app's, but the job says what it used)
ROS_ENV = ("ROS_PACKAGE_PATH", "AMENT_PREFIX_PATH")
HEALTH_SEP = " · "
#: joint types whose position the limits bound (a continuous joint wraps)
CLAMPED = ("revolute", "prismatic")
ACTUATED = ("revolute", "continuous", "prismatic")


#: the urdf view type (SPEC-43, ADR 0029): a web view beside the 3d view, its params the
#: 3d view's camera and environment plus the robot's own
URDF_MANIFEST = ViewManifest(
    type="urdf",
    label="URDF",
    host="web",
    doc="One robot description (.urdf), read-only: the picked item of its collection "
    "(or the first), stepping through the rest. Links and joints in a ROBOT panel "
    "(the tree nests links under their joints; a slider per revolute, continuous or "
    "prismatic joint, limits as its range; mimic joints follow), the 3d view's "
    "camera, environment and VIEW panel. Mesh paths (package://, file://, relative) "
    "resolve through params.package_paths, then the nearest package.xml naming the "
    "package, then ROS_PACKAGE_PATH and AMENT_PREFIX_PATH; never fetched over the "
    "network. A mesh not found draws as a placeholder, is listed by snap with its "
    "cause, and turns health amber. Pose it through params.joints ({joint name: "
    "value}, radians and metres); never edit the URDF. A click selects a link and "
    "sends <item key>#<link name> out; content is the selected link's or joint's "
    "properties; camera is shared with the 3d view.",
    accepts=["urdf"],
    cost=ViewCost(web_views=1, viewports3d=1),
    defaults={
        "joints": {},
        "view_mode": "lit",
        "up": "z",
        "environment": "none",
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
                "description": "The pose: {joint name: position}, radians for "
                "revolute and continuous joints, metres for prismatic ones (URDF "
                "units; the sliders show degrees and mm). Joints left out stay at 0, "
                "clamped into their limits; mimic joints follow their master. A "
                "slider release writes it as one undo step.",
            },
            "package_paths": {
                "type": ["object", "array"],
                "additionalProperties": {"type": "string"},
                "items": {"type": "string"},
                "description": "Where package://<name>/ meshes live, tried first: "
                "{name: folder} (the package's own folder), or a list of folders "
                "each searched for the package (a folder named for it, or a "
                "package.xml naming it). Relative folders are under the project.",
            },
            "view_mode": {
                "enum": ["lit", "unlit", "wireframe", "collision", "visual_collision"],
                "default": "lit",
                "description": "lit, unlit, wireframe, collision (only the colliders, "
                "each link in a flat tint) or visual_collision (the visuals lit, the "
                "colliders as translucent shells).",
            },
            "up": {
                "enum": ["x", "y", "z", "-x", "-y", "-z"],
                "default": "z",
                "description": "The robot's up axis; z for ROS (REP 103).",
            },
            "environment": {
                "enum": [ENV_NONE, *ENVIRONMENTS],
                "default": "none",
                "description": "Image-based lighting from one of the 3d view's "
                "bundled HDRIs (Poly Haven, CC0); none: the two default lights.",
            },
            "environment_intensity": {
                "type": "number",
                "minimum": 0,
                "maximum": ENV_INTENSITY_MAX,
                "default": 1.0,
                "description": "How strongly the environment lights the robot.",
            },
            "environment_background": {
                "type": "boolean",
                "default": False,
                "description": "Draw the environment behind the robot.",
            },
            "environment_rotation": {
                "type": "number",
                "minimum": 0,
                "maximum": 360,
                "default": 0.0,
                "description": "Turns the environment about the up axis, in degrees.",
            },
            "show_grid": {
                "type": "boolean",
                "default": True,
                "description": "The ground grid.",
            },
            "show_frames": {
                "type": "boolean",
                "default": False,
                "description": "RGB axes at each link's origin.",
            },
            "show_joint_axes": {
                "type": "boolean",
                "default": False,
                "description": "An arrow along each moving joint's axis.",
            },
            "show_com": {
                "type": "boolean",
                "default": False,
                "description": "A marker at each link's centre of mass, sized by mass.",
            },
            "camera_speed": {
                "type": "number",
                "minimum": 0.125,
                "maximum": 8,
                "default": 1.0,
                "description": "Scales flying, zooming and panning, as in the 3d view.",
            },
            "camera_mode": {
                "enum": ["free", "orbit"],
                "default": "free",
                "description": "free: Unreal Editor's camera, as in the 3d view; "
                "orbit: drag orbits the selection, middle-drag pans, the wheel dollies.",
            },
            "selected": {
                "type": ["string", "null"],
                "description": "The item key of the robot shown (default: the picked "
                "item, else the first).",
            },
            "selected_links": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "The selected links by name, the last one picked last; "
                "they go out on selection as <item key>#<link>. A click on the robot or a "
                "ROBOT panel row writes it.",
            },
            "panels": {
                "type": "object",
                "additionalProperties": True,
                "description": "The side panels' {collapsed, width} by name (view, robot).",
            },
        },
        "additionalProperties": True,
    },
    example={
        "view": {"type": "urdf", "label": "Arm", "params": {"joints": {"elbow": 1.2}}},
        "link": {
            "kind": "intaker",
            "scope": "selection",
            "source": {"view": "fege", "port": "selected_files"},
            "target": {"view": "robo", "port": "data"},
            "params": {"screener": ["@urdf"]},
            "summary": {"sentence": "The urdf view shows the robot picked in fege."},
        },
    },
)
URDF_PLUGIN = PluginManifest(name="app-urdf", version="0.1.0", views=[URDF_MANIFEST])


def unreadable_notes(failed: list[dict[str, Any]]) -> list[str]:
    """Health notes for the meshes the page could not read (``loaded.failed``): one per
    error, most meshes first."""
    counts: dict[str, int] = {}
    for f in failed:
        err = str(f.get("error") or "unreadable")
        counts[err] = counts.get(err, 0) + 1
    return [
        f"{n} mesh{'es' if n != 1 else ''} unreadable: {err}"
        for err, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def urdf_part(item: Item) -> str | None:
    for role in URDF_ROLES:
        if role in item.parts:
            return item.parts[role]
    return None


class RobotMeshes:
    """An item resolver serving the shown robot's meshes as extra roles of its item
    (``.m0``, ``.m1``, ...) beside the item's own parts; first in the runtime's chain,
    answering only for that one item of that one link."""

    def __init__(self) -> None:
        self.link = ""
        self.key: str | None = None
        self.table: dict[str, str] = {}

    def set(self, link: str, key: str | None, table: dict[str, str]) -> None:
        self.link, self.key, self.table = link, key, dict(table)

    def clear(self) -> None:
        self.set("", None, {})

    # -- ItemResolver ----------------------------------------------------------------------
    def parts(self, link_id: str, key: str) -> dict[str, str] | None:
        if not self.table or link_id != self.link or key != self.key:
            return None
        return dict(self.table)

    def keys(self, link_id: str) -> list[str]:
        return [self.key] if self.key and link_id == self.link and self.table else []


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
        # joints and time: declared, fed by the joint tracks of SPEC-43.3; until then the
        # pose comes from params.joints

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
            self.data_link, key, {**dict(it.parts), **{"." + r: p for r, p in files.items()}}
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
        notes.extend(unreadable_notes((self.loaded or {}).get("failed") or []))
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
        key = self.key
        keys = [f"{key}#{n}" for n in links] if key else []
        self.emit(SELECTION, Selection(keys=keys, last=keys[-1] if keys else None))
        self._emit_content()

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
        replaces ``params.joints`` as one user submit. Names the robot does not have,
        and values that are not numbers, are dropped; an unchanged map is no submit."""
        table = self._joint_table()
        joints: dict[str, float] = {}
        for name, raw in values.items():
            if name not in table:
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
    "UrdfHost",
    "factory",
    "register",
    "unreadable_notes",
    "urdf_part",
]
