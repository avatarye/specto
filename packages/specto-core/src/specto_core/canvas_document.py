"""Canvas document v2 (ADRs 0012, 0013, 0017): the one JSON document that completely
describes a project's one canvas, as a pydantic model.

Document shape (views and links are maps keyed by id, so patch paths stay stable)::

    { schema_version: 2,
      project:      {label},
      window_frame: {rect: {x, y, w, h}},
      root_view:    "rena" | null,
      views: { "fege": {type, label, layout: {position, size, floating?: {corner, offset,
                        by}, collapsed?}, params, locked}, ... },
      links: { "l1": {kind, source: {view, port}, target: {view, port}, two_way, params,
                      summary: {sentence, diagram?}, python?: {path, entry,
                      resolves_to, output}}, ... } }

Each link kind is its own model (a tagged union on ``kind``), so the JSON Schema shows
each kind's rules. Ports are declared per view type in ``ports.py``, never here. The
global clock is a runtime time base, not a document field. A view's id is its name
(``names.py``, ADR 0018): the app hands it out, the file tree's too (found by type).
The project source (``project``) is not in ``views``: it is the fixed node at the canvas
origin whose one output ``all_files`` every view must be reachable from by links (#83).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_serializer,
    model_validator,
)

from .errors import SchemaError
from .names import FILE_TREE_SALT, VIEW_ID_PATTERN, fresh_name, fresh_names
from .ports import (
    BUILTIN_KINDS,
    PLAYER_TYPES,
    PROJECT_ID,
    PROJECT_NODE_RECT,
    PROJECT_PORT,
    PROJECT_PORTS,
    SINGLE_TYPES,
    SOURCE_TYPES,
    TWO_WAY_PORT_TYPES,
    VIEW_PORTS,
    VIEW_ROLES,
    VIEW_ROLES_NOTE,
    VIEW_TYPES,
    PortType,
    ViewType,
)
from .schema import SCHEMA_VERSION

#: View ids are names (``VIEW_ID_PATTERN`` from ``names.py``: ``kobi``, the file tree
#: too); link ids are ``l<n>``; the map key is the id.
LINK_ID_PATTERN = r"^l[0-9]+$"


class Rect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = 0.0
    y: float = 0.0
    w: float = Field(gt=0)
    h: float = Field(gt=0)


#: the window corners a floating view is anchored to (ADR 0021)
Corner = Literal["top_left", "top_right", "bottom_left", "bottom_right"]


class Float(BaseModel):
    """A floating view (ADR 0021): it sits on the float layer, fixed in the window above
    the pannable canvas, at ``offset`` from its window ``corner``."""

    model_config = ConfigDict(extra="forbid")
    corner: Corner = Field(
        description="The window corner the view keeps its offset from (the nearest one), "
        "so it stays put when the window resizes."
    )
    offset: tuple[Annotated[float, Field(ge=0)], Annotated[float, Field(ge=0)]] = Field(
        default=(0.0, 0.0),
        description="Pixels from that corner to the view's own matching corner, inward "
        "(top_right [20, 10]: 20 px left of the window's right edge, 10 px below its top).",
    )
    by: Literal["user", "agent"] = Field(
        default="agent",
        description="Who floated it. A view the user floated may be unfloated by the agent "
        "but not moved or resized while it floats; only the user's Float gesture writes "
        "'user'.",
    )


class Layout(BaseModel):
    """Where a view sits on the canvas, in canvas pixels at 1:1. A floating view
    (``floating`` set) sits on the float layer instead; its ``position`` is where it lands
    when it is unfloated (ADR 0021)."""

    model_config = ConfigDict(extra="forbid")
    position: tuple[float, float] = (0.0, 0.0)
    size: tuple[float, float] = (400.0, 300.0)
    floating: Float | None = Field(
        default=None,
        description="Set: the view floats, fixed in the window above the pannable canvas "
        "(drawn above every canvas view, never panned or culled, always counted in the "
        "quotas). Absent or null: the view is on the canvas at position.",
    )
    collapsed: bool = Field(
        default=False,
        description="True: the view shows only its title bar, at the same width (#114). "
        "Its content is paused and costs no quota, its ports stay on the title bar and its "
        "links keep delivering; size keeps the height it expands back to. Absent or "
        "false: expanded.",
    )

    @model_serializer(mode="wrap")
    def _drop_defaults(self, handler: Any) -> dict[str, Any]:
        """A view on the canvas writes no ``floating`` key and an expanded one no
        ``collapsed``, so documents stay as they were."""
        out = handler(self)
        if out.get("floating") is None:
            out.pop("floating", None)
        if not out.get("collapsed"):
            out.pop("collapsed", None)
        return out


class View(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: ViewType = Field(
        description="The view type, with its role: "
        + ", ".join(f"{t} ({VIEW_ROLES[t]})" for t in VIEW_TYPES)
        + ". "
        + VIEW_ROLES_NOTE
    )
    label: str = ""
    layout: Layout = Field(default_factory=Layout)
    #: view-type-specific settings (gallery columns, map base layer, ...)
    params: dict[str, Any] = Field(default_factory=dict)
    #: a locked view refuses moves and deletions by the agent
    locked: bool = False


class PortRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: str = Field(
        description="A view id, or 'project' for the project source (output all_files only)."
    )
    port: str

    def __str__(self) -> str:
        return f"{self.view}.{self.port}"


class LinkSummary(BaseModel):
    """What the user reads instead of the link's code or parameters (glossary: link summary)."""

    model_config = ConfigDict(extra="forbid")
    sentence: str = Field(min_length=8, description="One plain sentence, written by the agent.")
    diagram: str | None = Field(default=None, description="Reserved: a small text diagram.")


class LinkBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    source: PortRef = Field(description="The output port the value comes from.")
    target: PortRef = Field(description="The input port the value feeds.")
    two_way: bool = Field(
        default=False,
        description="Sync both ways; only follow and time_map links on time or selection ports.",
    )
    summary: LinkSummary


class IntakerParams(BaseModel):
    """The intake spec: which of the files arriving on the input enter the collection and
    how they are keyed, grouped and sorted."""

    model_config = ConfigDict(extra="forbid")
    sources: list[str] = Field(
        default_factory=lambda: ["."],
        min_length=1,
        description="Paths or globs, relative to the project folder ('.' is all, the "
        "default). Scope pinned: the data the view shows. Scope selection: an optional "
        "narrowing of what the user picks.",
    )
    screener: list[str] = Field(
        default_factory=list,
        description="Include/exclude rules in order: '@images', '*.jpg', '!*.tmp', 're:^front/'.",
    )
    keys: str | None = Field(
        default=None,
        description="Regex with named groups (key fields) matched against the relative path.",
    )
    group_by: list[str] = Field(default_factory=list, description="Key fields defining an item.")
    sort: Literal["natural", "key", "mtime"] = "natural"
    watch: bool = Field(
        default=False,
        description="Auto-update: files arriving in the sources resolve in the background. "
        "Off by default (the user refreshes, or flips the view's switch); turn it on for "
        "folders an agent writes into. The file tree's intaker is on unless it says false.",
    )
    timeline: str | None = Field(
        default=None, description="The timeline this collection's time values are on."
    )


#: Glossary: intaker scope. ``selection``: the view follows the files the user picks in the
#: file tree (source ``files.selected_files``); ``pinned``: the view shows fixed sources
#: whatever is picked (source ``project.all_files``, the project source).
IntakerScope = Literal["selection", "pinned"]
INTAKER_SCOPES: tuple[str, ...] = ("selection", "pinned")
#: the source port each intaker scope is fed from
SCOPE_PORTS: dict[str, str] = {"selection": "selected_files", "pinned": PROJECT_PORT}
#: ... and where that port is, as the fix-its say it
SCOPE_SOURCES: dict[str, str] = {
    "selection": "a file tree's selected_files",
    "pinned": f"{PROJECT_ID}.{PROJECT_PORT}",
}


class IntakerLink(LinkBase):
    """files -> collection: selects files into a keyed, sorted collection. Every intaker
    but the file tree's own says its scope: 'selection' (the default choice: fed from the
    file tree's selected_files, the view shows whatever the user picks, screened and
    keyed; sources stay '.' or narrow the picks) or 'pinned' (fed from the project
    source's all_files, the view always shows the sources named, for data the user asked
    for by name: "always show the 02 run"). The file tree's intaker from project.all_files
    has no scope."""

    kind: Literal["intaker"]
    scope: IntakerScope | None = Field(
        default=None,
        description="'selection' follows the user's picks (source files.selected_files), "
        "'pinned' shows fixed sources (source project.all_files). Required except on the "
        "file tree's own intaker. Say which in the summary.",
    )
    params: IntakerParams = Field(default_factory=IntakerParams)


class FollowParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match: str | None = Field(
        default=None,
        description="Match items on this one key field (e.g. 'frame'); None: whole key.",
    )


class FollowLink(LinkBase):
    """Identity mapping between ports of the same type; selections match by key."""

    kind: Literal["follow"]
    params: FollowParams = Field(default_factory=FollowParams)


class ReadContentsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[str] = Field(min_length=1, description="Which of the input files to read.")
    format: str | None = Field(
        default=None, description="'geojson', 'json', 'text'; None: by extension."
    )


class ReadContentsLink(LinkBase):
    """files -> content: parses files (GeoJSON features, JSON, text) at resolve time."""

    kind: Literal["read_contents"]
    params: ReadContentsParams


class TimeMapParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_timeline: str = Field(description="Timeline of the source's time values.")
    to_timeline: str = Field(description="Timeline the target expects.")
    offset_ms: float = 0.0


class TimeMapLink(LinkBase):
    """time -> time: converts between timelines with an offset."""

    kind: Literal["time_map"]
    params: TimeMapParams


class PythonRef(BaseModel):
    """Python link code: a file under the project's ``.specto/links/`` and an entry point
    that returns the output data (JSON), by ``resolves_to``:

    - ``table``: ``entry(items, params, root)`` returns ``{source key: output value}``,
      looked up by the source's picked key;
    - ``samples``: ``entry(items, params, root)`` returns ``[[number, value], ...]``, the
      nearest number to the source's time picks;
    - ``value`` (per pick, #168): ``entry(value, items, params, root)`` returns the output
      value for the source's current value, and runs again (debounced) whenever that
      value changes. The value as the script sees it: files ``{root, paths}``, selection
      ``{keys, last}``, time a number of seconds, any other port its JSON.

    ``items`` are the source view's collection items (key, parts, meta; empty when the
    source has none, a file tree), ``params`` the link's params, ``root`` the project
    folder. Every output value is converted to the ``output`` port type: files from
    ``{root?, paths}`` or a list of paths; selection from a key, a list of keys or
    ``{keys, last?}``; content from ``{root?, paths}`` or a list of paths (the files are
    read; a folder stands for its readable files, a map's for its GeoJSON) or ``{root?,
    entries}``; time from a number of seconds; collection (a view's whole data, built as
    an intaker would) from ``{root?, items}`` or a list of items, an item a path or
    ``{key?, parts: {role: path} | [path, ...], keys?, meta?}``. ADR 0016: it runs at
    resolve time (when the file, the params, the source collection or, per pick, the
    value change), never per frame; ADR 0006: only
    after the user approved the file's exact content in the window (the view's Approve, or
    the approval card a ``link.approve`` request opens; the command itself never approves,
    #203), and out of
    process."""

    model_config = ConfigDict(extra="forbid")
    path: str = Field(
        pattern=r"^\.specto/links/[A-Za-z0-9_\-]+\.py$",
        description="The code file, relative to the project folder.",
    )
    entry: str = Field(
        default="resolve",
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description="The function in that file: entry(items, params, root), or "
        "entry(value, items, params, root) when resolves_to is value.",
    )
    resolves_to: Literal["table", "samples", "value"] = Field(
        description="The shape of the resolved output data: a lookup table, sampled values, "
        "or (value) the output for the source's current value, re-run per pick."
    )
    output: PortType = Field(description="The port type of the resolved values.")


class PythonLink(LinkBase):
    """Any port -> the declared output: Python code resolves to a table or samples."""

    kind: Literal["python"]
    python: PythonRef
    params: dict[str, Any] = Field(default_factory=dict)


Link = Annotated[
    IntakerLink | FollowLink | ReadContentsLink | TimeMapLink | PythonLink,
    Field(discriminator="kind"),
]

LINK_MODELS: dict[str, type[LinkBase]] = {
    "intaker": IntakerLink,
    "follow": FollowLink,
    "read_contents": ReadContentsLink,
    "time_map": TimeMapLink,
    "python": PythonLink,
}


class WindowFrame(BaseModel):
    """The app window's size on the canvas (the window-frame guide)."""

    model_config = ConfigDict(extra="forbid")
    rect: Rect = Field(default_factory=lambda: Rect(w=1600, h=900))


class ProjectMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""


class CanvasDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2]
    project: ProjectMeta = Field(default_factory=ProjectMeta)
    window_frame: WindowFrame = Field(default_factory=WindowFrame)
    root_view: str | None = Field(
        default=None, description="The player other views follow for time."
    )
    views: dict[Annotated[str, Field(pattern=VIEW_ID_PATTERN)], View] = Field(default_factory=dict)
    links: dict[Annotated[str, Field(pattern=LINK_ID_PATTERN)], Link] = Field(default_factory=dict)

    def to_data(self) -> dict[str, Any]:
        """The JSON form (what is written to ``canvas.json``)."""
        return self.model_dump(mode="json")

    @model_validator(mode="after")
    def _graph(self) -> CanvasDocument:
        """The port-graph rules, so that plain ``model_validate`` is strict too.
        ``validate_canvas`` reports the same problems with paths and fix-its."""
        problems = graph_problems(self.views, self.links, self.root_view)
        if problems:
            raise ValueError("\n".join(f"- {p.path}: {p.message} (fix: {p.fix})" for p in problems))
        return self


assert SCHEMA_VERSION == 2, "the canvas document model is schema version 2"


#: Where the file tree of a fresh canvas starts: right of the project node, which the app
#: draws fixed at the canvas origin (``PROJECT_NODE_RECT``).
FILE_TREE_X = 80
#: The fresh file tree's width; its height is the window frame's (``fit_fresh_tree``).
FILE_TREE_W = 280


def _covers_node(layout: Any) -> bool:
    """Whether a view's layout overlaps the project node's spot at the canvas origin."""
    try:
        (x, y), (w, h) = layout["position"], layout["size"]
    except (KeyError, TypeError, ValueError):
        return False
    nx, ny, nw, nh = PROJECT_NODE_RECT
    return x < nx + nw and nx < x + w and y < ny + nh and ny < y + h


def new_canvas_document(label: str = "") -> dict[str, Any]:
    """A fresh canvas: the file tree, fed by an intaker from the project source. The
    tree has a generated name like any view (#87); every fresh canvas gives it the same
    one, but code finds it by type (``file_tree_of``)."""
    tree = fresh_name(set(), FILE_TREE_SALT)
    return {
        "schema_version": SCHEMA_VERSION,
        "project": {"label": label},
        "window_frame": {"rect": {"x": 0, "y": 0, "w": 1600, "h": 900}},
        "root_view": None,
        "views": {
            tree: {
                "type": "file_tree",
                "label": "Files",
                "layout": {"position": [FILE_TREE_X, 0], "size": [FILE_TREE_W, 900]},
                "params": {},
                "locked": False,
            }
        },
        "links": {
            "l1": {
                "kind": "intaker",
                "label": "project files",
                "source": {"view": PROJECT_ID, "port": PROJECT_PORT},
                "target": {"view": tree, "port": "data"},
                "two_way": False,
                "params": {"sources": ["."], "watch": True},
                "summary": {"sentence": "The file tree shows every file in the project folder."},
            }
        },
    }


def file_tree_of(views: Mapping[str, Any]) -> str | None:
    """The id of the first file tree among ``views`` (a document's ``views`` map, as
    JSON or models), or None: the file tree is found by type, never by id (#87)."""
    for vid, view in views.items():
        vtype = view.get("type") if isinstance(view, dict) else getattr(view, "type", None)
        if vtype == "file_tree":
            return vid
    return None


def fit_fresh_tree(data: dict[str, Any]) -> bool:
    """A fresh canvas's file tree fills the visible canvas area (#125): while the
    canvas holds the file tree alone, where and as wide as ``new_canvas_document`` put
    it, its height follows the window frame's, in place. Once anything else is on the
    canvas, or the tree was moved, widened, floated or collapsed, the layout is the
    user's and stays. Returns whether the height changed."""
    views = data.get("views")
    if not isinstance(views, dict) or len(views) != 1:
        return False
    (vid,) = views
    lay = views[vid].get("layout") if _is_tree(views, vid) else None
    if not isinstance(lay, dict) or set(lay) != {"position", "size"}:
        return False
    (x, y), (w, h) = lay["position"], lay["size"]
    frame_h = data["window_frame"]["rect"]["h"]
    if (x, y, w) != (FILE_TREE_X, 0, FILE_TREE_W) or h == frame_h or frame_h <= 0:
        return False
    lay["size"] = [w, frame_h]
    return True


def _is_tree(views: Mapping[str, Any], view_id: Any) -> bool:
    view = views.get(view_id)
    return isinstance(view, dict) and view.get("type") == "file_tree"


def rewire_project_links(data: dict[str, Any]) -> list[str]:
    """Loading only (#83): a canvas saved before the project source had its one output
    fed pinned links from the file tree's ``all_files`` and the file tree from
    ``project.folder``. Both move to ``project.all_files``, in place; the ids moved come
    back in document order (empty when the canvas is already wired this way). Submits
    never pass through here: validation names the way out instead."""
    views = data.get("views")
    links = data.get("links")
    if not isinstance(views, dict) or not isinstance(links, dict):
        return []
    out: list[str] = []
    for lid, link in links.items():
        src = link.get("source") if isinstance(link, dict) else None
        if not isinstance(src, dict):
            continue
        old_project = src.get("view") == PROJECT_ID and src.get("port") == "folder"
        tree_all = src.get("port") == "all_files" and _is_tree(views, src.get("view"))
        if old_project or tree_all:
            link["source"] = {"view": PROJECT_ID, "port": PROJECT_PORT}
            out.append(lid)
    return out


@dataclass(frozen=True)
class ProjectFit:
    """What ``fit_project_source`` changed: the links moved to ``project.all_files``
    and how far the layout moved right (0: it did not)."""

    links: list[str]
    shift: float


def fit_project_source(data: dict[str, Any]) -> ProjectFit:
    """Loading only (#83): make a canvas saved before the project source fit it. Its
    links move to ``project.all_files`` (``rewire_project_links``) and, when the file
    tree covers the project node's spot at the canvas origin (where it used to sit),
    every view moves right by the same step so the leftmost lands at ``FILE_TREE_X``;
    the layout keeps its shape and the window frame stays."""
    links = rewire_project_links(data)
    views = data.get("views")
    if not isinstance(views, dict):
        return ProjectFit(links, 0.0)
    layouts = [v.get("layout") for v in views.values() if isinstance(v, dict)]
    trees = [v.get("layout") for vid, v in views.items() if _is_tree(views, vid)]
    if not any(_covers_node(lay) for lay in trees):
        return ProjectFit(links, 0.0)
    xs = [lay["position"][0] for lay in layouts if isinstance(lay, dict) and "position" in lay]
    shift = float(FILE_TREE_X - min(xs))
    for lay in layouts:
        if isinstance(lay, dict) and "position" in lay:
            x, y = lay["position"]
            lay["position"] = [x + shift, y]
    return ProjectFit(links, shift)


#: the name a project records in ``project.toml`` ``rewrites`` once its canvas turned the
#: file tree's auto-update on (#115), so the rewrite never overrides the user's later say
TREE_WATCH = "tree-watch"


def watch_the_file_tree(data: dict[str, Any], *, saved_before: bool = False) -> list[str]:
    """Loading and submits (#115): the file tree auto-updates by default. An intaker
    into a file tree that does not say ``watch`` turns it on; with ``saved_before``
    (loading a canvas saved before #115, when every intaker was written with ``watch:
    false``) a false one turns on too. Returns the link ids turned on."""
    views = data.get("views")
    links = data.get("links")
    if not isinstance(views, dict) or not isinstance(links, dict):
        return []
    out: list[str] = []
    for lid, link in links.items():
        if not isinstance(link, dict) or link.get("kind") != "intaker":
            continue
        dst = link.get("target")
        if not isinstance(dst, dict) or not _is_tree(views, dst.get("view")):
            continue
        params = link.setdefault("params", {})
        if not isinstance(params, dict):
            continue
        if "watch" not in params or (saved_before and params.get("watch") is False):
            params["watch"] = True
            out.append(lid)
    return out


def infer_intaker_scopes(data: dict[str, Any]) -> list[str]:
    """Loading only: a saved canvas from before intaker scopes (or written by hand) may
    have intakers without one. Fill it in from the source (the project source into any
    view but a file tree -> pinned, a file tree's ``selected_files`` -> selection) so
    the project still opens; the next save writes it. Returns the link ids filled in.
    Submits never pass through here: the agent must say the scope itself
    (``_scope_problems``)."""
    views = data.get("views")
    links = data.get("links")
    if not isinstance(views, dict) or not isinstance(links, dict):
        return []
    out: list[str] = []
    for lid, link in links.items():
        if not isinstance(link, dict) or link.get("kind") != "intaker":
            continue
        if link.get("scope") is not None:
            continue
        src, dst = link.get("source"), link.get("target")
        if not isinstance(src, dict) or not isinstance(dst, dict):
            continue
        scope: str | None = None
        if src.get("view") == PROJECT_ID and not _is_tree(views, dst.get("view")):
            scope = "pinned"
        elif _is_tree(views, src.get("view")) and src.get("port") == "selected_files":
            scope = "selection"
        if scope is not None:
            link["scope"] = scope
            out.append(lid)
    return out


def next_id(ids: Iterable[str], prefix: str) -> str:
    """The next free ``<prefix><n>`` after the ids in use (``l7`` after ``l1``..``l6``)."""
    used = [
        int(i[len(prefix) :]) for i in ids if i.startswith(prefix) and i[len(prefix) :].isdigit()
    ]
    return f"{prefix}{max(used, default=0) + 1}"


# ---------------------------------------------------------------------------------------
# Validation: every problem at once, each with a JSON-pointer path and a fix-it
# ---------------------------------------------------------------------------------------


class Problem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="JSON pointer into the document ('' is the whole document).")
    message: str
    fix: str


class CanvasInvalid(SchemaError):
    """A canvas document was rejected. ``problems`` lists every problem found (ADR 0013)."""

    code = "invalid_document"

    def __init__(self, problems: list[Problem]) -> None:
        self.problems: list[dict[str, str]] = [p.model_dump() for p in problems]
        n = len(problems)
        lines = "\n".join(f"- {p.path or '/'}: {p.message} (fix: {p.fix})" for p in problems)
        super().__init__(
            f"canvas document has {n} problem{'s' if n != 1 else ''}:\n{lines}",
            fix="fix every listed problem and submit again",
            problems=self.problems,
        )


def validate_canvas(data: dict[str, Any]) -> CanvasDocument:
    """Validate the JSON form; raise ``CanvasInvalid`` with every problem, or return the
    model. Field errors (pydantic) and port-graph errors arrive in the same reply."""
    doc, problems = _collect(data)
    if problems:
        raise CanvasInvalid(problems)
    assert doc is not None
    return doc


def validate_canvas_text(text: str) -> CanvasDocument:
    """``validate_canvas`` for JSON text, which also catches ids written twice (a JSON
    parser would silently keep the last one)."""
    data, problems = parse_canvas_text(text)
    if data is None:
        raise CanvasInvalid(problems)
    doc, more = _collect(data)
    problems.extend(more)
    if problems:
        raise CanvasInvalid(problems)
    assert doc is not None
    return doc


def parse_canvas_text(text: str) -> tuple[dict[str, Any] | None, list[Problem]]:
    """JSON text to the document's data plus the problems only the text shows: syntax
    (then the data is None) and keys written twice (the data keeps the last one)."""
    dups: dict[int, list[str]] = {}

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for k, v in pairs:
            if k in d:
                dups.setdefault(id(d), []).append(k)
            d[k] = v
        return d

    try:
        data = json.loads(text, object_pairs_hook=pairs_hook)
    except json.JSONDecodeError as e:
        return None, [
            Problem(
                path="",
                message=f"not valid JSON: {e.msg} (line {e.lineno}, column {e.colno})",
                fix="fix the JSON syntax there and submit again",
            )
        ]
    if not isinstance(data, dict):
        return None, [
            Problem(path="", message="the document is not a JSON object", fix="write {...}")
        ]
    problems: list[Problem] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key in dups.get(id(node), []):
                free = ""
                if path == "/links":
                    free = f"; the next free id is {next_id(node, 'l')}"
                elif path == "/views":
                    free = f"; a free name is {fresh_names(node, 1, key)[0]}"
                problems.append(
                    Problem(
                        path=path,
                        message=f"key {key!r} is written twice; the second entry silently "
                        "replaced the first",
                        fix=f"keep one entry per key{free}",
                    )
                )
            for k, v in node.items():
                walk(v, f"{path}/{_escape(k)}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}/{i}")

    walk(data, "")
    return data, problems


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


_LINK_ADAPTER: TypeAdapter[Any] = TypeAdapter(Link)


def _collect(data: dict[str, Any]) -> tuple[CanvasDocument | None, list[Problem]]:
    try:
        doc = CanvasDocument.model_validate(data)
    except ValidationError as e:
        errors = e.errors()
    else:
        return doc, []
    problems = [_field_problem(err) for err in errors if err["loc"] != ()]
    # Field errors stop pydantic before the graph rules run; check the graph over the
    # views and links that did parse so the agent sees both layers at once.
    views: dict[str, View] = {}
    bad: set[str] = set()
    raw_views = data.get("views")
    if isinstance(raw_views, dict):
        for vid, raw in raw_views.items():
            try:
                views[vid] = View.model_validate(raw)
            except ValidationError:
                bad.add(vid)
    links: dict[str, LinkBase] = {}
    raw_links = data.get("links")
    if isinstance(raw_links, dict):
        for lid, raw in raw_links.items():
            with contextlib.suppress(ValidationError):
                links[lid] = _LINK_ADAPTER.validate_python(raw)
    root = data.get("root_view")
    problems.extend(graph_problems(views, links, root if isinstance(root, str) else None, bad))
    return None, problems


def _field_problem(err: Any) -> Problem:
    """One pydantic error as a problem with a fix-it the agent can act on."""
    loc = [str(x) for x in err["loc"] if x != "[key]"]
    kind = err["type"]
    ctx = err.get("ctx", {})
    # a tagged union puts the kind into the location: ('links', 'l9', 'python', 'summary')
    if len(loc) >= 3 and loc[0] == "links" and loc[2] in LINK_MODELS:
        del loc[2]
    field = loc[-1] if loc else ""
    path = "/" + "/".join(_escape(x) for x in loc)
    msg = err["msg"]
    kinds = ", ".join(LINK_MODELS)
    if kind in ("union_tag_invalid", "union_tag_not_found"):
        path += "/kind"
        tag = ctx.get("tag")
        msg = f"unknown link kind {tag!r}" if tag is not None else "a link needs a kind"
        return Problem(path=path, message=msg, fix=f"set kind to one of: {kinds}")
    if kind == "missing":
        fix = f"add {field!r}"
        if field == "summary":
            fix = "add summary: {sentence: one plain sentence saying what the link does}"
        elif field == "python":
            fix = (
                "add python: {path: .specto/links/<name>.py, entry: <function>, "
                "resolves_to: table|samples|value, output: <port type>}"
            )
        elif field == "params":
            fix = f"add params for this link kind (describe link:<kind> lists them: {kinds})"
        return Problem(path=path, message=f"{field!r} is required", fix=fix)
    if kind == "extra_forbidden":
        return Problem(path=path, message=f"{field!r} is not a field here", fix=f"remove {field!r}")
    if kind in ("literal_error", "enum"):
        return Problem(path=path, message=msg, fix=f"use one of: {ctx.get('expected', '')}")
    if kind == "string_pattern_mismatch":
        if loc == ["views", PROJECT_ID]:
            fix = (
                f"remove it: {PROJECT_ID!r} is the project source, the fixed node the app "
                f"draws at the canvas origin, not a view; link from {PROJECT_ID}.{PROJECT_PORT}"
            )
        elif loc[:1] == ["views"] and len(loc) == 2:
            fix = (
                "a view id is its name, handed out by the app (two syllables such as "
                "kobi; the file tree has one too); submit a new view under any "
                "placeholder key and the app names it"
            )
        elif loc[:1] == ["links"] and len(loc) == 2:
            fix = (
                "link ids are l<n> (l1, l2, ...); submit a new link under any placeholder "
                "key and the app numbers it"
            )
        elif field == "path":
            fix = "put the code under .specto/links/<name>.py"
        else:
            fix = f"match {ctx.get('pattern', 'the pattern')}"
        return Problem(path=path, message=msg, fix=fix)
    if kind == "string_too_short":
        return Problem(path=path, message=msg, fix="write one plain sentence")
    if kind == "too_short":
        return Problem(path=path, message=msg, fix="list at least one entry")
    if kind in ("greater_than", "greater_than_equal"):
        return Problem(path=path, message=msg, fix="use a positive number")
    return Problem(path=path, message=msg, fix="describe canvas shows the schema")


def graph_problems(
    views: Mapping[str, View],
    links: Mapping[str, LinkBase],
    root_view: str | None,
    unchecked: Iterable[str] = (),
) -> list[Problem]:
    """The rules a schema cannot state: ports exist and match the link kind, one link per
    input port, two-way only where allowed, the root view is a player, no cycles on the
    port graph, at most one view of each ``SINGLE_TYPES`` type (the scratch pad).
    ``unchecked`` names views that failed their own validation; links
    touching them are not judged twice. Reachability from the project source binds the
    agent only and is judged at submit (``reach_problems``), not here."""
    unchecked = set(unchecked)
    out: list[Problem] = []

    if root_view is not None and root_view not in unchecked:
        rv = views.get(root_view)
        players = ", ".join(sorted(PLAYER_TYPES))
        if rv is None:
            out.append(
                Problem(
                    path="/root_view",
                    message=f"root_view {root_view!r} is not a view id",
                    fix=f"set root_view to a player view ({players}) or null",
                )
            )
        elif rv.type not in PLAYER_TYPES:
            out.append(
                Problem(
                    path="/root_view",
                    message=f"root_view {root_view} is a {rv.type}; the root view must be a player",
                    fix=f"set root_view to a {players} view or null",
                )
            )

    for vtype in sorted(SINGLE_TYPES):
        same = [vid for vid, v in views.items() if v.type == vtype]
        for vid in same[1:]:
            out.append(
                Problem(
                    path=f"/views/{vid}",
                    message=f"{vid} is a second {vtype} view; a canvas holds at most one "
                    f"({same[0]})",
                    fix=f"use {same[0]}, or remove it first",
                )
            )

    inputs_taken: dict[str, str] = {}
    for lid, link in links.items():
        out.extend(_link_problems(lid, link, views, unchecked, inputs_taken))

    cycle = _find_cycle(views, links)
    if cycle:
        out.append(
            Problem(
                path="/links",
                message="links form a cycle: " + " -> ".join(cycle),
                fix="break the loop: remove one link, or make one link two_way: true and remove "
                "the reverse link (two-way only on "
                + "/".join(sorted(TWO_WAY_PORT_TYPES))
                + " ports)",
            )
        )
    return out


def _port_type(
    ref: PortRef, views: Mapping[str, View], unchecked: set[str], direction: str
) -> tuple[str | None, str | None, str | None]:
    """(port type, problem message, fix); all None when the view is unchecked."""
    if ref.view == PROJECT_ID:
        decl, what = PROJECT_PORTS, "the project source"
    elif ref.view in unchecked:
        return None, None, None
    elif ref.view in views:
        decl, what = VIEW_PORTS[views[ref.view].type], f"{views[ref.view].type} {ref.view}"
    else:
        known = ", ".join(views) or "none yet"
        return (
            None,
            f"{ref} names unknown view {ref.view!r}",
            f"use one of the view ids ({known})"
            + (f" or {PROJECT_ID!r}" if direction == "output" else ""),
        )
    table = decl.outputs if direction == "output" else decl.inputs
    if ref.port not in table:
        have = ", ".join(table) or "none"
        fix = f"use one of: {have}"
        source = f"{PROJECT_ID}.{PROJECT_PORT}"
        if ref.view == PROJECT_ID:
            fix = f"the project source's one output is {source}; use it"
        elif direction == "output" and decl is VIEW_PORTS["file_tree"] and ref.port == "all_files":
            fix = (
                f"all files come from the project source: pin from {source} (scope 'pinned'), "
                f"or follow the picks from {ref.view}.selected_files (scope 'selection')"
            )
        return None, f"{ref}: {what} has no {direction} port {ref.port!r} (has: {have})", fix
    return table[ref.port], None, None


def _kinds_mapping(src_t: str, dst_t: str) -> str:
    fits = [
        k
        for k, (need_in, need_out) in BUILTIN_KINDS.items()
        if (need_in is None and src_t == dst_t) or (need_in == src_t and need_out == dst_t)
    ]
    fits.append("python")
    return ", ".join(fits)


def _link_problems(
    lid: str,
    link: LinkBase,
    views: Mapping[str, View],
    unchecked: set[str],
    inputs_taken: dict[str, str],
) -> list[Problem]:
    out: list[Problem] = []
    base = f"/links/{lid}"
    src_t, msg, fix = _port_type(link.source, views, unchecked, "output")
    if msg:
        out.append(Problem(path=f"{base}/source", message=msg, fix=fix or ""))
    if link.target.view == PROJECT_ID:
        out.append(
            Problem(
                path=f"{base}/target",
                message="the project folder has no input ports",
                fix="target a view's input port",
            )
        )
        dst_t = None
    else:
        dst_t, msg, fix = _port_type(link.target, views, unchecked, "input")
        if msg:
            out.append(Problem(path=f"{base}/target", message=msg, fix=fix or ""))

    if src_t is not None and dst_t is not None:
        kind: str = link.kind  # type: ignore[attr-defined]
        if isinstance(link, PythonLink):
            if link.python.output != dst_t:
                out.append(
                    Problem(
                        path=f"{base}/python/output",
                        message=f"python resolves to {link.python.output!r} but target "
                        f"{link.target} takes {dst_t!r}",
                        fix=f"set python.output to {dst_t!r}, or target a "
                        f"{link.python.output!r} port",
                    )
                )
        else:
            need_in, need_out = BUILTIN_KINDS[kind]
            maps = f"{kind} maps {need_in or 'T'!r} -> {need_out or 'T'!r}"
            alt = (
                f"use a link kind that maps {src_t!r} -> {dst_t!r} ({_kinds_mapping(src_t, dst_t)})"
            )
            if need_in is not None and src_t != need_in:
                out.append(
                    Problem(
                        path=f"{base}/source",
                        message=f"{maps} but source {link.source} is {src_t!r}",
                        fix=f"connect a {need_in!r} output, or {alt}",
                    )
                )
            if need_out is not None and dst_t != need_out:
                out.append(
                    Problem(
                        path=f"{base}/target",
                        message=f"{maps} but target {link.target} takes {dst_t!r}",
                        fix=f"connect a {need_out!r} input, or {alt}",
                    )
                )
            if need_in is None and src_t != dst_t:
                out.append(
                    Problem(
                        path=base,
                        message=f"{kind} is an identity mapping; source {link.source} is "
                        f"{src_t!r} but target {link.target} takes {dst_t!r}",
                        fix=alt,
                    )
                )

    if isinstance(link, IntakerLink):
        out.extend(_scope_problems(base, link, views, unchecked))

    if link.two_way:
        out.extend(_two_way_problems(base, link, views, unchecked, dst_t))

    if link.target.view != PROJECT_ID and link.target.view not in unchecked:
        key = str(link.target)
        if key in inputs_taken:
            out.append(
                Problem(
                    path=f"{base}/target",
                    message=f"input port {key} is already fed by link {inputs_taken[key]}; "
                    "an input port takes one link",
                    fix=f"remove this link or link {inputs_taken[key]}, or feed another input",
                )
            )
        else:
            inputs_taken[key] = lid
    return out


def _scope_problems(
    base: str, link: IntakerLink, views: Mapping[str, View], unchecked: set[str]
) -> list[Problem]:
    """Every intaker but the file tree's own declares its scope, and the scope and the
    source agree: selection <-> a file tree's selected_files, pinned <-> project.all_files."""
    src, dst = link.source, link.target
    project_source = f"{PROJECT_ID}.{PROJECT_PORT}"
    if src.view == PROJECT_ID:
        feeds_tree = dst.view in unchecked or (
            dst.view in views and views[dst.view].type == "file_tree"
        )
        if link.scope == "selection":
            return [
                Problem(
                    path=f"{base}/scope",
                    message="the project source has no selection; scope selection needs a "
                    "file tree's selected_files as the source",
                    fix="set scope to 'pinned' (the view always shows params.sources), or "
                    "feed this intaker from a file tree's selected_files port",
                )
            ]
        if link.scope is None and not feeds_tree:
            return [
                Problem(
                    path=f"{base}/scope",
                    message=f"an intaker fed from {project_source} into {dst.view} shows fixed "
                    "files whatever the user picks: say so",
                    fix="set scope: 'pinned' (and name the data in params.sources), or feed "
                    f"{dst.view} from a file tree's selected_files with scope 'selection' so "
                    "it follows the user's picks",
                )
            ]
        return []
    if src.view in unchecked or src.view not in views or views[src.view].type != "file_tree":
        return []
    if link.scope is None:
        return [
            Problem(
                path=f"{base}/scope",
                message=f"an intaker fed from file tree {src.view} needs a scope: does the "
                "view follow what the user picks, or show fixed files?",
                fix="set scope: 'selection' (the default choice; source port selected_files, "
                f"the view shows whatever the user picks) or 'pinned' (source {project_source}, "
                "the view always shows params.sources)",
            )
        ]
    if link.scope == "pinned":
        return [
            Problem(
                path=f"{base}/source",
                message=f"scope 'pinned' is fed from {project_source}, but the source is "
                f"{src.view}.{src.port}",
                fix=f"set source to {project_source}, or scope to 'selection' (source port "
                "selected_files: the view follows the user's picks)",
            )
        ]
    want = SCOPE_PORTS[link.scope]
    if src.port != want:
        return [
            Problem(
                path=f"{base}/source",
                message=f"scope {link.scope!r} is fed from {want}, but the source is {src.port}",
                fix=f"set source.port to {want!r}",
            )
        ]
    return []


def unreachable_views(data: Mapping[str, Any]) -> set[str]:
    """The views of a canvas document (JSON form) that no walk from the project source
    reaches along the links, both ways along a two-way one (#83): what the canvas shows
    as "not connected". A view of a ``SOURCE_TYPES`` type (the scratch pad, #184) is a
    source of its own: reached, and so is what it feeds."""
    views = data.get("views") or {}
    edges: dict[str, set[str]] = {}
    for link in (data.get("links") or {}).values():
        s, t = link["source"]["view"], link["target"]["view"]
        edges.setdefault(s, set()).add(t)
        if link.get("two_way"):
            edges.setdefault(t, set()).add(s)
    # the project source, and the views that are sources of their own (the scratch pad)
    reached = {PROJECT_ID} | {
        vid for vid, v in views.items() if isinstance(v, dict) and v.get("type") in SOURCE_TYPES
    }
    frontier = list(reached)
    while frontier:
        for nxt in edges.get(frontier.pop(), ()):
            if nxt not in reached:
                reached.add(nxt)
                frontier.append(nxt)
    return {vid for vid in views if vid not in reached}


def reach_problems(
    old: Mapping[str, Any], new: CanvasDocument, origin_is_user: bool
) -> list[Problem]:
    """Every view hangs off the project source by links (#83), and that binds the agent
    only, like a lock (user decision): the user's gestures may leave a view "not
    connected". A submit by anyone else is refused for each view it leaves unreachable
    that was reachable in the live document ``old``, or that it adds, with the link that
    would reach it: an intaker (or read_contents) into its file input, or, when an
    unreachable view already feeds it, that view. A view the user already left
    unconnected does not block an unrelated agent submit."""
    if origin_is_user:
        return []
    data = new.to_data()
    before = unreachable_views(old)
    views, links = new.views, new.links
    trees = [vid for vid, v in views.items() if v.type == "file_tree"]
    out: list[Problem] = []
    for vid in sorted(unreachable_views(data), key=list(views).index):
        if vid in before:
            continue
        view = views[vid]
        feeders = sorted(
            {ln.source.view for ln in links.values() if ln.target.view == vid}
            | {ln.target.view for ln in links.values() if ln.two_way and ln.source.view == vid}
        )
        feeders = [f for f in feeders if f != vid]
        if feeders:
            fix = (
                f"{vid} follows {', '.join(feeders)}: link {feeders[0]} to the project source "
                "(or to a view that is reachable from it)"
            )
        else:
            fix = _suggested_link(vid, view, trees)
        out.append(
            Problem(
                path=f"/views/{vid}",
                message=f"view {vid} ({view.type}) is not reachable from the project source "
                "by links",
                fix=fix,
            )
        )
    return out


def _suggested_link(vid: str, view: View, trees: list[str]) -> str:
    """The link that would feed ``view`` its files: by the port it takes them on."""
    source = f"{PROJECT_ID}.{PROJECT_PORT}"
    inputs = VIEW_PORTS[view.type].inputs
    port = "data" if "data" in inputs else next(iter(inputs), None)
    if port is None:
        return f"link {vid} from a view that is reachable from the project source"
    dst_t = inputs[port]
    kinds = [
        k
        for k, (need_in, need_out) in BUILTIN_KINDS.items()
        if need_in == "files" and need_out == dst_t
    ]
    if "intaker" in kinds:
        tree = f"{trees[0]}.selected_files" if trees else "a file tree's selected_files"
        return (
            f"add an intaker from {tree} to {vid}.{port} with scope 'selection' (the view "
            f"follows the user's picks), or from {source} with scope 'pinned' (fixed sources)"
        )
    if kinds:
        return f"add a {kinds[0]} link from {source} to {vid}.{port}"
    return (
        f"link {vid}.{port} from a view that is reachable from the project source "
        f"(a {dst_t!r} output)"
    )


def _two_way_problems(
    base: str, link: LinkBase, views: Mapping[str, View], unchecked: set[str], dst_t: str | None
) -> list[Problem]:
    path = f"{base}/two_way"
    kind: str = link.kind  # type: ignore[attr-defined]
    allowed = "/".join(sorted(TWO_WAY_PORT_TYPES))
    if kind not in ("follow", "time_map"):
        return [
            Problem(
                path=path,
                message=f"two_way is only allowed on follow or time_map links, not {kind}",
                fix="set two_way: false",
            )
        ]
    if dst_t is None:
        return []
    if dst_t not in TWO_WAY_PORT_TYPES:
        return [
            Problem(
                path=path,
                message=f"two_way is not allowed on {dst_t!r} ports (only {allowed})",
                fix="set two_way: false",
            )
        ]
    rev_src, p1, _ = _port_type(
        PortRef(view=link.target.view, port=link.target.port), views, unchecked, "output"
    )
    rev_dst, p2, _ = _port_type(
        PortRef(view=link.source.view, port=link.source.port), views, unchecked, "input"
    )
    if p1 or p2 or rev_src != rev_dst:
        return [
            Problem(
                path=path,
                message=f"two_way needs {link.target} as an output and {link.source} as an "
                "input of the same type",
                fix="set two_way: false, or link two views that both input and output this port",
            )
        ]
    return []


def _find_cycle(views: Mapping[str, View], links: Mapping[str, LinkBase]) -> list[str] | None:
    """Cycle search on the port graph: one-way link edges plus each view type's declared
    input -> output propagation. Two-way links are the sanctioned loops and stay out."""
    edges: dict[str, list[str]] = {}

    def add(a: str, b: str) -> None:
        edges.setdefault(a, []).append(b)
        edges.setdefault(b, [])

    # an input and an output may share a name (video.time), so nodes carry direction
    for link in links.values():
        if not link.two_way:
            add(f"{link.source}(out)", f"{link.target}(in)")
    for vid, v in views.items():
        for inp, outs in VIEW_PORTS[v.type].propagates.items():
            for o in outs:
                add(f"{vid}.{inp}(in)", f"{vid}.{o}(out)")

    white, grey, black = 0, 1, 2
    color = dict.fromkeys(edges, white)
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        color[n] = grey
        stack.append(n)
        for m in edges[n]:
            if color[m] == grey:
                return [*stack[stack.index(m) :], m]
            if color[m] == white:
                found = visit(m)
                if found:
                    return found
        stack.pop()
        color[n] = black
        return None

    for n in list(edges):
        if color[n] == white:
            found = visit(n)
            if found:
                return found
    return None


# ---------------------------------------------------------------------------------------
# What agents read: the JSON Schema and per-kind / per-port descriptions (ADR 0013)
# ---------------------------------------------------------------------------------------


def canvas_json_schema() -> dict[str, Any]:
    """The canvas document's JSON Schema (``api.info`` and ``describe canvas``)."""
    return CanvasDocument.model_json_schema()


def describe_link_kind(kind: str) -> dict[str, Any]:
    """One link kind: its own schema, what it maps, and the worked example's link of it."""
    from .canvas_example import worked_example

    model = LINK_MODELS[kind]
    if kind == "python":
        maps = {"source": "any", "target": "python.output"}
    else:
        need_in, need_out = BUILTIN_KINDS[kind]
        maps = {"source": need_in or "any", "target": need_out or "same type as the source"}
    links = [link for link in worked_example()["links"].values() if link["kind"] == kind]
    out = {
        "kind": kind,
        "doc": (model.__doc__ or "").strip(),
        "maps": maps,
        "two_way": kind in ("follow", "time_map"),
        "schema": model.model_json_schema(),
        "example": links[0] if links else None,
    }
    if kind == "intaker":
        # one example per scope, so the agent sees both wirings off the file tree
        out["scopes"] = {
            "selection": "fed from a file tree's selected_files: the view follows what the "
            "user picks (the default choice for views next to the file tree)",
            "pinned": "fed from the project source's all_files: the view always shows "
            "params.sources (only when the user names specific data)",
        }
        out["examples"] = {
            scope: next((ln for ln in links if ln.get("scope") == scope), None)
            for scope in INTAKER_SCOPES
        }
    if kind == "python":
        # how to write the code: per-pick values in, port shapes out, a worked example
        from .python_link_guide import python_link_guide

        out["guide"] = python_link_guide()
    return out


def describe_port_type(port_type: str) -> dict[str, Any]:
    """One port type: whether links on it may be two-way and which view types carry it."""
    inputs_of = sorted(t for t, d in VIEW_PORTS.items() if port_type in d.inputs.values())
    outputs_of = sorted(t for t, d in VIEW_PORTS.items() if port_type in d.outputs.values())
    if port_type in PROJECT_PORTS.outputs.values():
        outputs_of.insert(0, PROJECT_ID)
    kinds = [
        k
        for k, (need_in, need_out) in BUILTIN_KINDS.items()
        if need_in is None or port_type in (need_in, need_out)
    ]
    return {
        "type": port_type,
        "two_way": port_type in TWO_WAY_PORT_TYPES,
        "inputs_of": inputs_of,
        "outputs_of": outputs_of,
        "link_kinds": [*kinds, "python"],
    }
