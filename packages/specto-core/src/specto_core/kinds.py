"""Data-kind vocabulary, reader protocols and manifests (HLD 3.7).

Readers are two things: a **Locator** (cheap; ``can_handle(path) -> priority``,
``locate(item) -> DataRef``) and a **Decoder** (``decode(ref, request) -> TypedBuffer``,
runs in a worker process). Views declare the kinds they accept in a ``ViewManifest``.
Manifests are plain data, readable without importing plugin code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .ports import CONTEXT_VIEWER, VIEW_ROLES


def _hdri_names() -> list[str]:
    """The 3d view's HDRI environments (specto-web's ``web/3d/environments.json``), in
    its order; none where specto-web is not installed."""
    try:
        import specto_web
    except ImportError:  # pragma: no cover - the workspace installs it
        return []
    return [str(e["name"]) for e in specto_web.environments()]


_HDRI_NAMES = _hdri_names()
_HDRI_DEFAULT = "sunny" if "sunny" in _HDRI_NAMES else (_HDRI_NAMES[0] if _HDRI_NAMES else None)
#: the usd view's ``hdri`` param (SPEC-31): one of the 3d view's HDRIs
_HDRI_SCHEMA: dict[str, Any] = {
    **({"enum": _HDRI_NAMES} if _HDRI_NAMES else {"type": ["string", "null"]}),
    "default": _HDRI_DEFAULT,
    "description": "The HDRI when environment is hdri (and Storm's stand-in for a sky).",
}

DATA_KINDS: tuple[str, ...] = (
    "image2d",
    "image_seq",
    "video_ref",
    "pointcloud",
    "mesh",
    "splat",
    "usd_stage_ref",
    "geojson",
    "json",
    "text",
    "markdown",
    "html",
    "pdf",
    "table",
    "message_stream",
    "pose_track",
    "joint_track",
)
DataKind = Literal[
    "image2d",
    "image_seq",
    "video_ref",
    "pointcloud",
    "mesh",
    "splat",
    "usd_stage_ref",
    "geojson",
    "json",
    "text",
    "markdown",
    "html",
    "pdf",
    "table",
    "message_stream",
    "pose_track",
    "joint_track",
]

HostKind = Literal["quick", "web", "frame"]


class DataRef(BaseModel):
    """What a locator returns: enough for a view to take the reference or ask a decoder."""

    model_config = ConfigDict(extra="forbid")
    kind: DataKind
    uri: str  # file path, or container entry like drive.mcap#/camera/image
    meta: dict[str, Any] = Field(default_factory=dict)  # size, shape hints, mime, stamp


class Item(BaseModel):
    """One collection item as intake produces it: a key plus its parts (paths)."""

    model_config = ConfigDict(extra="forbid")
    key: str
    parts: dict[str, str] = Field(default_factory=dict)  # role (ext or name) -> path
    index: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def primary(self) -> str:
        return next(iter(self.parts.values()))


class BufferSpec(BaseModel):
    """Describes a typed buffer. ``shm`` names a shared-memory block; ``None`` = inline."""

    model_config = ConfigDict(extra="forbid")
    dtype: str
    shape: list[int]
    shm: str | None = None
    offset: int = 0
    layout: str = ""  # e.g. "HWC", "xyz", "arrow"
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def nbytes(self) -> int:
        return int(np.dtype(self.dtype).itemsize * int(np.prod(self.shape)))


class TypedBuffer:
    """A decoded result: a ``BufferSpec`` plus the array (inline or mapped from shm).

    The worker side fills ``array`` into shared memory and ships the spec; the GUI
    side rebuilds the view with ``attach``. Zero-copy either way.
    """

    def __init__(self, spec: BufferSpec, array: np.ndarray | None = None) -> None:
        self.spec = spec
        self.array = array

    @classmethod
    def from_numpy(cls, arr: np.ndarray, *, layout: str = "", **meta: Any) -> TypedBuffer:
        return cls(
            BufferSpec(dtype=str(arr.dtype), shape=list(arr.shape), layout=layout, meta=meta),
            np.ascontiguousarray(arr),
        )

    @classmethod
    def attach(cls, spec: BufferSpec, buf: memoryview | bytes) -> TypedBuffer:
        arr = np.frombuffer(
            buf, dtype=np.dtype(spec.dtype), count=int(np.prod(spec.shape)), offset=spec.offset
        ).reshape(spec.shape)
        return cls(spec, arr)

    def to_numpy(self) -> np.ndarray:
        if self.array is None:
            raise ValueError("buffer is not attached; call TypedBuffer.attach(spec, memory)")
        return self.array


class DecodeRequest(BaseModel):
    """What a view wants from a decoder: a thumbnail, a full frame, a range of points."""

    model_config = ConfigDict(extra="forbid")
    purpose: Literal["thumbnail", "full", "range", "meta"] = "full"
    max_size: int | None = None  # longest edge for images
    start: int | None = None
    count: int | None = None
    options: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class Locator(Protocol):
    name: str

    def can_handle(self, path: str) -> int:
        """Priority > 0 if this locator handles ``path``; 0 means no."""
        ...

    def locate(self, item: Item) -> DataRef: ...


@runtime_checkable
class Decoder(Protocol):
    name: str

    def decode(self, ref: DataRef, request: DecodeRequest) -> TypedBuffer: ...


class ViewCost(BaseModel):
    """What a live instance of a view costs against the per-canvas quotas (HLD 4.4)."""

    model_config = ConfigDict(extra="forbid")
    web_views: int = 0
    viewports3d: int = 0
    video_decoders: int = 0
    resident_points: int = 0
    #: a view rendering with RTX (ovrtx) in its own process: one GPU-heavy renderer
    rtx_views: int = 0


class ViewManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    label: str = ""
    #: what the view shows and what picks it: the agent's reference card carries it, so
    #: it stays short (SPEC-56)
    doc: str = ""
    #: the longer guidance (controls, how the params play together, what happens on
    #: this machine): ``describe view:<type>`` shows it, the reference leaves it out
    guide: str = ""
    accepts: list[DataKind] = Field(default_factory=list)
    host: HostKind = "quick"
    cost: ViewCost = Field(default_factory=ViewCost)
    defaults: dict[str, Any] = Field(default_factory=dict)  # default ``params``
    state_schema: dict[str, Any] = Field(default_factory=dict)  # JSON schema of ``params``
    example: dict[str, Any] = Field(default_factory=dict)  # example view entry for describe
    #: machine capabilities the type renders with when present (``specto_core.machine``);
    #: ``describe`` says whether this machine has them
    uses: list[str] = Field(default_factory=list)
    #: a dev/test view type: left out of every human list; ``describe`` still offers it
    hidden: bool = False
    plugin: str = "builtin"


class LocatorManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    extensions: list[str] = Field(default_factory=list)  # ".jpg"
    aliases: list[str] = Field(default_factory=list)  # "@images"
    kinds: list[DataKind] = Field(default_factory=list)
    priority: int = 10
    plugin: str = "builtin"


class DecoderManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kinds: list[DataKind] = Field(default_factory=list)  # input ref kinds
    purposes: list[str] = Field(default_factory=lambda: ["full"])
    produces: str = "ndarray"
    plugin: str = "builtin"


class PluginManifest(BaseModel):
    """npe2-style manifest: what a plugin contributes and needs, readable without import."""

    model_config = ConfigDict(extra="forbid")
    name: str
    version: str = "0.0.0"
    api_min: int = 1
    api_max: int = 1
    requires: list[str] = Field(default_factory=list)  # capabilities: ovrtx, cuda, webengine
    views: list[ViewManifest] = Field(default_factory=list)
    locators: list[LocatorManifest] = Field(default_factory=list)
    decoders: list[DecoderManifest] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    intake_aliases: dict[str, list[str]] = Field(default_factory=dict)  # "@images" -> exts


class PluginRegistry:
    """Holds manifests. ``describe`` and quotas read from here; the app and intake register."""

    def __init__(self) -> None:
        self.plugins: dict[str, PluginManifest] = {}
        self._views: dict[str, ViewManifest] = {}
        self._locators: dict[str, LocatorManifest] = {}
        self._decoders: dict[str, DecoderManifest] = {}
        self._disabled: dict[str, str] = {}

    def register(self, manifest: PluginManifest) -> None:
        if manifest.name in self.plugins:
            raise ValueError(f"plugin {manifest.name!r} already registered")
        roleless = [v.type for v in manifest.views if v.type not in VIEW_ROLES]
        if roleless:
            raise ValueError(
                f"plugin {manifest.name!r}: view type(s) {', '.join(roleless)} declare no role; "
                "add each to specto_core.ports.VIEW_ROLES (ADR 0020)"
            )
        self.plugins[manifest.name] = manifest
        for v in manifest.views:
            self._views[v.type] = v.model_copy(update={"plugin": manifest.name})
        for lo in manifest.locators:
            self._locators[lo.name] = lo.model_copy(update={"plugin": manifest.name})
        for d in manifest.decoders:
            self._decoders[d.name] = d.model_copy(update={"plugin": manifest.name})

    def disable(self, name: str, reason: str) -> None:
        self._disabled[name] = reason

    def disabled(self) -> dict[str, str]:
        return dict(self._disabled)

    def view(self, view_type: str) -> ViewManifest | None:
        return self._views.get(view_type)

    def views(self) -> list[ViewManifest]:
        return [self._views[k] for k in sorted(self._views)]

    def locators(self) -> list[LocatorManifest]:
        return [self._locators[k] for k in sorted(self._locators)]

    def decoders(self) -> list[DecoderManifest]:
        return [self._decoders[k] for k in sorted(self._decoders)]

    def aliases(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for p in self.plugins.values():
            for alias, exts in p.intake_aliases.items():
                out.setdefault(alias, []).extend(exts)
        for lo in self._locators.values():
            for alias in lo.aliases:
                out.setdefault(alias, []).extend(lo.extensions)
        return {k: sorted(set(v)) for k, v in out.items()}

    def ext_kinds(self) -> dict[str, list[str]]:
        """``{extension: [data kinds]}`` over every locator, highest priority first; plain
        data so an intaker resolve can take it into a worker process."""
        out: dict[str, list[str]] = {}
        for lo in sorted(self._locators.values(), key=lambda m: -m.priority):
            for ext in lo.extensions:
                lst = out.setdefault(ext.lower(), [])
                lst.extend(k for k in lo.kinds if k not in lst)
        return out

    def kinds_for_extension(self, ext: str) -> list[DataKind]:
        ext = ext.lower()
        out: list[DataKind] = []
        for lo in sorted(self._locators.values(), key=lambda m: -m.priority):
            if ext in lo.extensions:
                out.extend(k for k in lo.kinds if k not in out)
        return out

    def kinds_for_path(self, path: str) -> list[DataKind]:
        return self.kinds_for_extension(Path(path).suffix)

    def typed_view_for(self, path: str) -> str | None:
        """The view type that shows a file of ``path``'s kind in a view of its own (SPEC-32:
        a dedicated view), or None when no registered view type does. Candidates are the
        registered view types that accept one of the path's kinds, except the browsers
        (they show many items) and the context viewer (it follows the picks by design);
        the kinds are tried in the locators' order (a .md file is markdown before text),
        and among the candidates for one kind the narrowest wins (the fewest accepted
        kinds: the markdown view over the json inspector, the map over the json
        inspector for geojson), ties by type name. One table: the manifests."""
        for kind in self.kinds_for_path(path):
            found = [
                m
                for m in self._views.values()
                if kind in m.accepts
                and m.type != CONTEXT_VIEWER
                and VIEW_ROLES.get(m.type) != "browser"
            ]
            if found:
                return min(found, key=lambda m: (len(m.accepts), m.type)).type
        return None


# ---------------------------------------------------------------------------
# Built-in manifests. The app registers its real view hosts against these types; core
# only needs the vocabulary (accepts, host, cost). Ports are declared in ``ports.py``.

BUILTIN_VIEWS: list[ViewManifest] = [
    ViewManifest(
        type="gallery",
        label="Gallery",
        host="quick",
        doc="Tile gallery of a collection of any kind (images; video poster frames, first "
        "pdf pages, text excerpts, 3D kind icons).",
        guide="Drag to pan, wheel to zoom; a native Qt Quick item.",
        accepts=[
            "image2d",
            "image_seq",
            "video_ref",
            "pdf",
            "mesh",
            "pointcloud",
            "splat",
            "markdown",
            "html",
            "json",
            "geojson",
            "text",
        ],
        defaults={"tile": 192, "zoom": 1.0},
        example={"type": "gallery", "label": "Cam A", "params": {"tile": 192}},
    ),
    ViewManifest(
        type="json",
        label="JSON inspector",
        host="web",
        doc="Monaco read-only JSON view with folding and outline; shows the selected item "
        "of its collection.",
        accepts=["json", "text", "markdown", "geojson"],
        cost=ViewCost(web_views=1),
        defaults={"wrap": False},
        example={"type": "json", "label": "Sidecar", "params": {"wrap": False}},
    ),
    # the host, its frame-source process and the QML chrome are later tickets (#132)
    ViewManifest(
        type="usd",
        label="USD",
        host="frame",
        doc="One OpenUSD stage (.usd, .usda, .usdc, .usdz), read-only: the picked item "
        "of its collection (or the first), stepping through the rest. Rendered out of "
        "process: RTX (ovrtx) when this machine has the ovrtx capability, otherwise Hydra "
        "Storm. A click selects the prim under the cursor and sends <item key>#<prim "
        "path> out; content is the selected prim's properties. Time is the stage's "
        "timecodes in seconds; the camera is shared with the 3d view. Nothing is written "
        "to the file.",
        guide="Navigation as in the 3d view. camera_mode free is Unreal Editor's camera: "
        "drag turns and walks, right-drag looks and WASD flies, middle-drag pans, the "
        "wheel dollies, Alt+drag orbits the selection; orbit: drag orbits the selection's "
        "centre, else the point under the press, else the focus point, middle-drag pans, "
        "right-drag and the wheel dolly toward it; switching keeps the camera where it is. "
        "F frames the selected prims' whole subtrees, else everything. look_through takes "
        "a camera prim; orbiting drops back to the free camera. renderer auto is rtx on "
        "Windows and Linux when this machine has ovrtx, else storm (any GPU, no RTX look); "
        "on macOS always storm; renderer_auto below says what auto runs here. mode is "
        "RTX's: rt real-time path tracing, pt accumulating path tracing (spp samples per "
        "pixel per frame), minimal; storm ignores it. view_mode collision shows only the "
        "prims with UsdPhysics CollisionAPI, as their collision shapes in a flat tint (a "
        "stage without colliders shows lit); wireframe draws edges over black. environment "
        "is composed over the stage in the viewer, never written, and kept across stages: "
        "stage uses its own lights (a stage without any gets a plain dome); sky is one of "
        "NVIDIA's dynamic procedural skies with a sun (RTX only: Storm shows the HDRI "
        "instead), whose time_of_day, cloudiness and haze move it live (null: the sky's "
        "own) and whose ground is the skies' preview floor (10 x 20 m, top at 0, in the "
        "stage's units); hdri is one of the 3d view's Poly Haven environments. The skies "
        "(CloudySky's textures are 67 MB, fetched last) and the HDRIs are fetched on first "
        "use into the user's Specto folder; snap reports what shows, the fetch's progress "
        "and a note when the choice is not what shows yet.",
        accepts=["usd_stage_ref"],
        # rtx_views counts only where the renderer resolves to rtx: not storm, nor auto
        # on macOS or without ovrtx (quotas.view_cost_of, SPEC-36)
        cost=ViewCost(viewports3d=1, rtx_views=1),
        uses=["ovrtx", "storm"],
        defaults={
            "renderer": "auto",
            "mode": "rt",
            "look_through": None,
            "camera_speed": 1.0,
            "camera_mode": "free",
            "max_pixels": 1920 * 1080,
            "spp": 1,
            "view_mode": "lit",
            "environment": "stage",
            "sky": "ClearSky",
            "hdri": _HDRI_DEFAULT,
            "time_of_day": None,
            "cloudiness": None,
            "haze": None,
            "ground": False,
        },
        state_schema={
            "type": "object",
            "properties": {
                "renderer": {
                    "enum": ["auto", "rtx", "storm"],
                    "default": "auto",
                    "description": "auto is rtx where this machine has ovrtx, else storm "
                    "(describe view:usd reports renderer_auto).",
                },
                "mode": {
                    "enum": ["rt", "pt", "minimal"],
                    "default": "rt",
                    "description": "RTX render mode (rt real-time, pt accumulating path "
                    "tracing); storm ignores it.",
                },
                "look_through": {
                    "type": ["string", "null"],
                    "default": None,
                    "description": "A camera prim path to look through; null: the free camera.",
                },
                "camera_speed": {
                    "type": "number",
                    "minimum": 0.125,
                    "maximum": 8,
                    "default": 1.0,
                    "description": "Scales flying, zooming and panning.",
                },
                "camera_mode": {
                    "enum": ["free", "orbit"],
                    "default": "free",
                    "description": "free: Unreal Editor's camera; orbit: drag turns around "
                    "the selection.",
                },
                "view_mode": {
                    "enum": ["lit", "unlit", "wireframe", "collision"],
                    "default": "lit",
                    "description": "What the picture shows; collision: only the "
                    "UsdPhysics colliders.",
                },
                "environment": {
                    "enum": ["stage", "sky", "hdri"],
                    "default": "stage",
                    "description": "stage: its own lights; sky: an NVIDIA dynamic sky "
                    "named by sky (RTX only); hdri: one of the 3d view's HDRIs named by "
                    "hdri.",
                },
                "sky": {
                    "enum": [
                        "ClearSky",
                        "Cirrus",
                        "CumulusLight",
                        "CumulusHeavy",
                        "Overcast",
                        "NightSky",
                        "CloudySky",
                    ],
                    "default": "ClearSky",
                    "description": "The dynamic sky when environment is sky.",
                },
                "hdri": _HDRI_SCHEMA,
                "ground": {
                    "type": "boolean",
                    "default": False,
                    "description": "With a sky: the skies' preview floor under the stage.",
                },
                "time_of_day": {
                    "type": ["number", "null"],
                    "minimum": 0,
                    "maximum": 24,
                    "default": None,
                    "description": "The sky's hour, moving its sun live; null: the sky's own.",
                },
                "cloudiness": {
                    "type": ["number", "null"],
                    "minimum": 0,
                    "maximum": 1,
                    "default": None,
                    "description": "The sky's cumulus cover, live; null: the sky's own.",
                },
                "haze": {
                    "type": ["number", "null"],
                    "minimum": 0,
                    "maximum": 1,
                    "default": None,
                    "description": "The sky's haze, live; null: the sky's own.",
                },
                "selected": {
                    "type": ["string", "null"],
                    "description": "The item key of the stage shown; unset: the pick, "
                    "else the first.",
                },
                "max_pixels": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 1920 * 1080,
                    "description": "Cap on rendered pixels.",
                },
                "spp": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 1,
                    "description": "Samples per pixel per frame in pt mode.",
                },
                "panels": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "The side panels' {collapsed, width} by name (VIEW, PRIMS).",
                },
            },
            "additionalProperties": True,
        },
        example={
            "view": {"type": "usd", "label": "World", "params": {"renderer": "auto"}},
            "links": [
                {
                    "kind": "intaker",
                    "scope": "selection",
                    "source": {"view": "fege", "port": "selected_files"},
                    "target": {"view": "kobi", "port": "data"},
                    "params": {"screener": ["@usd"]},
                    "summary": {"sentence": "The usd view shows the stage picked in fege."},
                },
                {
                    "kind": "time_map",
                    "source": {"view": "tamu", "port": "time"},
                    "target": {"view": "kobi", "port": "time"},
                    "params": {"from_timeline": "video", "to_timeline": "usd"},
                    "summary": {"sentence": "Scrubbing the video tamu moves the stage's time."},
                },
            ],
        },
    ),
    # the project's scratch pad (#184): at most one per canvas, items in .specto/scratch
    ViewManifest(
        type="scratch",
        label="Scratch pad",
        host="web",
        doc="The project's scratch pad, at most one per canvas: named pages (tabs) of "
        "markdown, each one file .specto/scratch/<page>.md, rendered like the markdown view "
        "and editable in place. You read and edit pages with scratch_read, scratch_append "
        "and scratch_write. A source of its own: it needs no input link and counts as "
        "reachable from the project. The page shown (params.page) goes out on items (the "
        "page's markdown, then the files and pictures it links: a gallery or preview can "
        "follow it); a link the user clicks goes out on selection.",
        guide="A rendered / source toggle, autosaved, with the markdown view's outline and "
        "zoom. What the user drops or pastes (Ctrl+V) from any view or another app is "
        "appended as markdown: text as text, pictures embedded from <page>.assets/, files "
        "and prims as links relative to the page, an item from a view as a short quote "
        "with its source. Every change is an undo step. A clicked link shows in the pad's "
        "preview beside it.",
        cost=ViewCost(web_views=1),
        state_schema={
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "description": "The page shown and sent out on items, by id (p2) or "
                    "name; unset: the pad's active page.",
                }
            },
            "additionalProperties": True,
        },
        example={
            "view": {"type": "scratch", "label": "scratch pad", "params": {"page": "lidar"}},
            "link": {
                "kind": "follow",
                "source": {"view": "sapo", "port": "items"},
                "target": {"view": "kobi", "port": "data"},
                "summary": {"sentence": "The gallery shows the scratch pad's items."},
            },
        },
    ),
]

BUILTIN_LOCATORS: list[LocatorManifest] = [
    LocatorManifest(
        name="image",
        extensions=[".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"],
        aliases=["@images"],
        kinds=["image2d"],
        priority=10,
    ),
    LocatorManifest(
        name="json", extensions=[".json"], aliases=["@json"], kinds=["json"], priority=10
    ),
    LocatorManifest(
        name="geojson",
        extensions=[".geojson"],
        aliases=["@geo"],
        kinds=["geojson", "json"],
        priority=20,
    ),
    LocatorManifest(
        name="text",
        extensions=[".txt", ".csv", ".yaml", ".yml", ".toml", ".log"],
        aliases=["@text"],
        kinds=["text"],
        priority=5,
    ),
    LocatorManifest(
        name="markdown",
        extensions=[".md", ".markdown"],
        aliases=["@markdown"],
        kinds=["markdown", "text"],
        priority=10,
    ),
    # what the html view shows: a page with its relative assets next to it
    LocatorManifest(
        name="html",
        extensions=[".html", ".htm"],
        aliases=["@html"],
        kinds=["html", "text"],
        priority=10,
    ),
    # what the pdf view shows: pages rendered by QtPdf in the app, no decoder in core
    LocatorManifest(name="pdf", extensions=[".pdf"], aliases=["@pdf"], kinds=["pdf"], priority=10),
    # containers expand into virtual entries at scan time (specto_core.containers); the
    # archive itself has no data kind, its members keep theirs
    LocatorManifest(
        name="container",
        extensions=[".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz"],
        aliases=["@archives"],
        kinds=[],
        priority=1,
    ),
    LocatorManifest(
        name="video",
        extensions=[".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"],
        aliases=["@video"],
        kinds=["video_ref"],
        priority=10,
    ),
    # what the 3d view shows; a .ply is a mesh, a point cloud or a splat by its header,
    # which the intaker's resolve step reads (specto_readers.scene3d); .stl and .obj go to
    # Babylon's loaders, a .dae is converted to a GLB at resolve (SPEC-42)
    LocatorManifest(
        name="scene3d",
        extensions=[".ply", ".glb", ".gltf", ".splat", ".spz", ".sog", ".stl", ".obj", ".dae"],
        aliases=["@3d"],
        kinds=["mesh", "pointcloud", "splat"],
        priority=10,
    ),
    # what the usd view shows, by extension only: the usd_info decoder reads the root
    # layer's header, the view's source process opens the stage
    LocatorManifest(
        name="usd",
        extensions=[".usd", ".usda", ".usdc", ".usdz"],
        aliases=["@usd"],
        kinds=["usd_stage_ref"],
        priority=10,
    ),
    # a table of joint positions over time (SPEC-43.3: the urdf view's joints port); the
    # lowest priority, so a .csv stays text for every view that does not ask for a track
    LocatorManifest(
        name="joint_track", extensions=[".csv", ".parquet"], kinds=["joint_track"], priority=1
    ),
]

BUILTIN_DECODERS: list[DecoderManifest] = [
    DecoderManifest(name="image", kinds=["image2d"], purposes=["thumbnail", "full", "meta"]),
    DecoderManifest(name="json", kinds=["json", "geojson"], purposes=["full"], produces="bytes"),
    DecoderManifest(
        name="text", kinds=["text", "markdown", "html"], purposes=["full"], produces="bytes"
    ),
    # the root layer's header as JSON (up axis, units, time codes, default prim), without
    # pxr; what needs the composed stage is listed as unavailable
    DecoderManifest(name="usd_info", kinds=["usd_stage_ref"], purposes=["meta"], produces="bytes"),
    # a time column plus one column per joint as one float64 table, time-sorted, in seconds
    DecoderManifest(name="joint_track", kinds=["joint_track"], purposes=["full"]),
]


def builtin_plugin_manifest() -> PluginManifest:
    return PluginManifest(
        name="builtin",
        version="0.1.0",
        views=list(BUILTIN_VIEWS),
        locators=list(BUILTIN_LOCATORS),
        decoders=list(BUILTIN_DECODERS),
    )


def default_plugins() -> PluginRegistry:
    reg = PluginRegistry()
    reg.register(builtin_plugin_manifest())
    return reg
