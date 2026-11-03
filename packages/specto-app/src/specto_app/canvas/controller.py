"""The canvas controller: the canvas document's normal view as QML models, and the
user's gestures as submits (ADRs 0012, 0013; spec #35 stories 31, 32).

Direction of data is one way: submits change the document, the live canvas emits
patches, the controller rebuilds its models from ``engine.live.data``. It never writes
the document itself; a gesture (close a view, undo) goes through
``specto_core.gestures`` or ``LiveCanvas.undo`` with origin ``user`` and is one undo
step on the same history as the agent's submits.

Two models feed the QML: ``viewList`` (the view ids as a row model: the canvas area and
the overworld make one card per row, and a card lives as long as its view) and
``views`` (per-view facts the card binds to: layout rect, label, type, health, lock,
on screen). ``viewIds`` is the same list with the rects, for Python callers.

The normal view (#40) pans at 1:1: ``pan`` is the canvas point shown at the area's
padded top-left corner, ``viewport`` the area as a canvas rect. The human pans with a
middle drag, or by holding Space and dragging with the left button, anywhere over the
area, views included (``spaceHeld``, read by the controller's event filter on the window;
never while a text field has the focus, so Space still types). Space is the canvas's: a
press-and-drag only pans, a tap with no drag goes on to the focused view (a gallery's
quick look, a video's play / pause); Home and End go to the focused view first and a
Home no view takes pans home (#113, ``eventFilter``). The wheel never pans and
the right button is reserved for a context menu. Views have a selected status
(``selectedViews``, ``views[*].selected``): a header click selects, Shift or Ctrl toggles,
a left drag on the empty canvas is a marquee (``selectInRect``), the empty canvas or Esc
clears. The selection is navigation state (no undo step) and travels in the prompt
bar's canvas context so "these" resolves. The status bar speaks for one view at a time
(``statusHint``, #80): the hovered one (``hoveredView``), else the last selected one. The
hovered view is resolved from the pointer by the window event filter (every mouse move,
mapped through the area's origin and the pan onto the live rects: a host that accepts
hover events, the gallery's native node for one, would otherwise swallow the hover before
any slot-level handler saw it); ``hoverView``/``leaveView`` set it directly for tools and
tests. The status line says the view's health message, its
empty-state hint and, for a hovered view with nothing else to say, the gestures the
card takes. The header itself shows only ``<id>: <label>``, the health dot and the
buttons. Once a pan settles the
window frame's origin follows it (``LiveCanvas.navigate``: navigation, not an undo
step), so the frame guide is where the window is and the submit-time quota counts
exactly the views that render. Views whose rect misses the frame are off-screen: their
host is paused and their slot hidden; panned back, they resume. ``maximize`` fills the
area with one view (the others pause) until Esc restores the layout (story 72). Move
and resize gestures snap (``canvas.snapping``) against the other views and the
window-frame guide, then submit through ``specto_core.gestures``; the header's lock is a
submit too (``gestures.set_locked``).

Link visuals (#45): ``wires`` is one curve per link in canvas px with its chain colour
(``canvas.chains``), ``views[*].chain`` the colour token of the view's link chain. The
normal view draws no curves (#81): it shows links as identicons on the port circles,
``views[*].inPorts`` one per linked input with the upstream view's identicon and hover
tip, ``views[*].icon`` the view's own for its output circle (``canvas.ports_info``). The
wiring layer (#82) draws them over the normal view while ``wiring`` is on: the backtick
or tilde key toggles it (the controller's event filter on the window, never while a text
field has the focus), Esc leaves it, and it is navigation state like the selection. The
overworld (``canvas.overworld``) draws the same wires. While a move or resize gesture
runs (``dragMove``/``dragResize``, or the overworld's free drags through
``set_drag_rect``) the wires are built from the dragged rect, so curves and link nodes
follow the view live; the drop or ``cancelDrag`` clears it. A header drag on one view of
a multi-selection moves the whole selection (#84): the dragged view snaps, the others
follow by the same delta through ``dragRects`` and the drop is one ``move_views`` submit
(locked views included; the lock binds the agent). ``wireIds`` and ``wireMap``
let the QML keep one curve item per link across those rebuilds. The wiring layer's
link nodes sit at ``nodePlaces`` (#90; the chips report their sizes, ``setNodeSize``):
on the wire clear of the views, else clear of their headers, else nudged off them, never
on a port circle, the overworld's rule (``chains.NodePlacer``). Port capsules (#160,
``canvas.capsules``): ``capsuleIds`` and ``capsuleMap`` list each link's pair of port
circles (the output and the input it feeds, both showing the source's identicon) at the
drawn rects, and whether a capsule joins them: within the join distance it joins, and
it holds across a drag's steps until the break distance. While the overworld
shows, ``set_all_paused`` keeps every host paused whatever the viewport.

The float layer (#112, ADR 0021): a floating view sits fixed in the window above the
canvas at ``views[*].fx/fy`` (window px, from its corner offset and the area's live
size), so a pan or a window resize leaves it where it is. ``rect_of`` gives it the canvas
rect under it at the current pan, so the wires cross the two layers, hover finds it
(above the canvas views) and culling never pauses it. Its header drag and border drag
snap in window px to the window edges and the other floaters only (canvas views snap to
neither), stay inside the window and drop as ``place_floating``; ``toggleFloat`` is the
header's Float / Unfloat, after the pan has settled into the window frame's origin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QCoreApplication,
    QEvent,
    QModelIndex,
    QObject,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QKeyEvent

from specto_core import Engine
from specto_core.canvas_document import unreachable_views
from specto_core.errors import SpectoError
from specto_core.float_layer import is_floating, shown_size, window_position
from specto_core.gestures import (
    auto_update_of,
    close_view,
    float_view,
    is_contextual,
    move_views,
    open_scratch,
    place_floating,
    resize_view,
    set_auto_update,
    set_collapsed,
    set_locked,
    unfloat_view,
)
from specto_core.links import FilesValue
from specto_core.ports import PROJECT_ID, PROJECT_PORT

from ..hosts.stepping import step_hint
from ..theme import METRICS
from .capsules import CapsuleTracker, port_pairs
from .chains import (
    NODE_PAD_PX,
    PROJECT_NODE,
    NodePlacer,
    chain_tokens,
    chains_of,
    project_refresh_anchor,
    wire_geometry,
)
from .identicon import identicon_url
from .ports_info import (
    input_ports,
    link_tip,
    output_linked,
    output_tip,
    project_tip,
    unlinked_input_tip,
)
from .snapping import SNAP_PX, Rect, Snapped, snap_move, snap_resize, visible_ids

#: the name the project source's identicon is generated from (user decision on #83): the
#: node itself shows the Specto mark, its output port and the inputs it feeds this grid
ROOT_SEED = "root"

if TYPE_CHECKING:
    from specto_app.hosts.registry import HostRegistry
    from specto_app.notices import Notices

PAD = 12.0  # theme.metrics.pad: the gap between the area's edge and the canvas origin
NAV_SETTLE_MS = 250  # a pan writes the window frame's origin once it has settled
#: the wiring layer's key: backtick, or tilde with Shift (the same key on a US layout)
WIRING_KEYS = (Qt.Key.Key_QuoteLeft, Qt.Key.Key_AsciiTilde)
GESTURES = (
    "drag the header to move, a border to resize, double-click to maximise, "
    "hold Space and drag to pan, ` wiring layer"
)
#: a view type's own keys, said while it is hovered (the 3D page's in-body hint moved
#: here with its bar, #129); the usd view navigates as the 3d view does (#139):
#: Unreal Editor's viewport (#151, #152; the table is web/viewnav.js's header); in orbit
#: mode it says its own line (``UsdHost.nav_keys``, #39)
NAV_KEYS = (
    "drag turns and walks, right-drag looks (W A S D Q E fly, wheel sets speed), "
    "middle-drag pans, wheel dollies, Alt+drag orbits, F frames"
)
VIEW_KEYS = {"3d": NAV_KEYS, "usd": NAV_KEYS}
#: the status bar's line while the wiring layer shows (#82)
WIRING_HINT = "wiring layer: ` or Esc leaves, click a link dot to open the link"
HINT_SEP = "  ·  "
#: a view no link reaches from the project source (the user unlinked it; #83): its health
#: and the status bar's line until a link reaches it again
DISCONNECTED = "disconnected"
DISCONNECTED_MESSAGE = "not connected"
DISCONNECTED_HINT = "link it from the file tree or ask the agent"
#: a view fed by an intaker says whether it follows its files on its own (#102)
AUTO_UPDATE_ON = "auto-update on"
AUTO_UPDATE_OFF = "auto-update off · refresh"
#: a hovered floating view says where it lives (#112)
FLOATING_HINT = "floating: fixed in the window while the canvas pans, Unfloat drops it here"
#: a collapsed view (#114): its title bar alone, paused, its links still delivering
COLLAPSED_H = float(METRICS["collapsed_h"])
COLLAPSED = "collapsed"
COLLAPSED_HINT = "collapsed · expand"
#: view types whose header has no auto-update (#126): the preview follows the picks it
#: is fed, it never watches files of its own
NO_AUTO_UPDATE = frozenset({"preview"})


def status_hint(info: dict[str, Any], *, hovered: bool) -> str:
    """The status bar's line for one view (``views[*]`` facts): the health message
    (resolving, paused, error, needs approval), the empty-state hint, whether a view fed
    by an intaker auto-updates (#102), and for a hovered healthy view the gestures (a
    viewer's or player's step keys first, #107, then the type's own keys, #129); empty
    when the view has nothing to say."""
    parts: list[str] = []
    health, msg = info.get("health", "ok"), info.get("healthMessage", "")
    if info.get("collapsed") and health in ("ok", "paused"):
        # its pause is the collapse: say so, and how to undo it (#114)
        return f"{info.get('id', '')}: {COLLAPSED_HINT}"
    if health == "loading":
        parts.append("resolving" + (f": {msg}" if msg else ""))
    elif health == "error":
        parts.append(f"error: {msg}" if msg else "error")
    elif health == "approval":
        parts.append(msg or "needs approval")  # "l3: approve <path> to let it run"
    elif health == "paused":
        parts.append(msg or "paused")
    elif msg:
        parts.append(msg)
    if info.get("approval") and health != "approval":
        parts.append(f"approve {info['approval']} to let it run")
    if info.get("hint"):
        parts.append(str(info["hint"]))
    auto = info.get("autoUpdate")
    if auto is not None:
        parts.append(AUTO_UPDATE_ON if auto else AUTO_UPDATE_OFF)
    if hovered and info.get("floating"):
        parts.append(FLOATING_HINT)
    if hovered and health == "ok" and not info.get("hint") and not info.get("approval"):
        # a healthy view offers its gestures while hovered: a viewer's step keys first,
        # then the host's own line for its state (the usd view's camera mode, #39), else
        # the type's
        steps = step_hint(str(info.get("stepType") or info.get("type", "")))
        own = str(info.get("keys") or VIEW_KEYS.get(str(info.get("type", "")), ""))
        parts.extend([h for h in (steps, own) if h] + [GESTURES])
    return f"{info.get('id', '')}: {HINT_SEP.join(parts)}" if parts else ""


def _editing_text(window: QObject) -> bool:
    """A text field has the window's focus: a native one (the prompt bar, a path field)
    or a web view (Monaco in the json and markdown views types there). The shell window
    decides (``Main.qml`` ``editingText``): the focus item is never wrapped for Python,
    since inside a web view it is Chromium's render delegate, and wrapping that segfaults
    on Linux. A window without the property has no text field of the shell's."""
    return bool(window.property("editingText"))


class ViewList(QAbstractListModel):
    """The view ids as a list model (role ``vid``) that changes by rows: a view that
    appears is one insert, one that goes is one remove, and nothing else touches the
    list. A Repeater over a plain JS array rebuilds every delegate on any change, which
    destroyed the card whose release handler was still running (its slot, the host item
    re-parented, a QML TypeError at every gesture end)."""

    VID = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ids: list[str] = []

    @property
    def ids(self) -> list[str]:
        return list(self._ids)

    def set_ids(self, ids: list[str]) -> None:
        wanted = set(ids)
        for i in range(len(self._ids) - 1, -1, -1):
            if self._ids[i] not in wanted:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._ids[i]
                self.endRemoveRows()
        for i, vid in enumerate(ids):
            if i < len(self._ids) and self._ids[i] == vid:
                continue
            if vid in self._ids:  # reordered: move it here
                j = self._ids.index(vid)
                self.beginMoveRows(QModelIndex(), j, j, QModelIndex(), i)
                self._ids.insert(i, self._ids.pop(j))
                self.endMoveRows()
            else:
                self.beginInsertRows(QModelIndex(), i, i)
                self._ids.insert(i, vid)
                self.endInsertRows()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._ids)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._ids):
            return None
        if role in (self.VID, Qt.ItemDataRole.DisplayRole):
            return self._ids[index.row()]
        return None

    def roleNames(self) -> dict[int, bytes]:
        return {self.VID: b"vid"}


class CanvasController(QObject):
    viewIdsChanged = Signal()
    viewsChanged = Signal()
    statusChanged = Signal()
    focusChanged = Signal()
    panChanged = Signal()
    maximizedChanged = Signal()
    snapGuidesChanged = Signal()
    dragRectsChanged = Signal()
    wiresChanged = Signal()
    wireIdsChanged = Signal()
    wiringChanged = Signal()
    wiringEscape = Signal()  # Esc while the wiring layer shows: the layer decides
    nodesChanged = Signal()  # the wiring layer's link nodes moved (#90)
    capsulesChanged = Signal()  # a port capsule joined, split or moved (#160)
    capsuleIdsChanged = Signal()
    spaceHeldChanged = Signal()
    selectionChanged = Signal()
    projectNodeChanged = Signal()
    hintChanged = Signal()  # the hovered view or the status bar's line changed
    flash = Signal(str)  # pulse the card of this view (fly-to, a changed view)
    patched = Signal(str)  # meta kind, for tests and status

    def __init__(
        self,
        engine: Engine,
        *,
        hosts: HostRegistry | None = None,
        notices: Notices | None = None,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.live = engine.live
        self.hosts = hosts
        self.notices = notices
        self._view_ids: list[dict[str, Any]] = []
        self._view_list = ViewList(self)
        self._views: dict[str, dict[str, Any]] = {}
        self._focused = ""
        self._pan = (0.0, 0.0)
        self._area = (0.0, 0.0)  # the canvas area's size in pixels (QML reports it)
        self._origin = (0.0, 0.0)  # the area's top-left in window pixels (QML reports it)
        self._offscreen: set[str] = set()  # the views this controller paused
        self._all_paused = False  # the overworld shows: nothing renders
        self._guides: list[dict[str, Any]] = []  # snap lines hit by the gesture in progress
        self._chains: dict[str, str] = {}  # view id -> chain colour token
        self._chain_nums: dict[str, int] = {}  # view id -> chain number (identicon colour)
        self._wires: list[dict[str, Any]] = []
        self._wire_ids: list[str] = []
        # the port capsules (#160): the joined pairs across a drag's steps
        self._capsules = CapsuleTracker()
        self._capsule_map: dict[str, dict[str, Any]] = {}
        self._capsule_ids: list[str] = []
        self._drag_rects: dict[str, Rect] = {}  # views mid-gesture: the rect they follow
        self._maximized = ""  # the view filling the area (story 72), "" for none
        self._wiring = False  # the wiring layer shows over the normal view (#82)
        # the wiring layer's link nodes (#90): off the headers and the port circles
        self._nodes = NodePlacer(METRICS["header_h"], NODE_PAD_PX)
        self._space_held = False  # Space is down outside a text field: drags pan
        self._space_pressed = False  # a button went down while Space was held: no tap
        self._forwarding = False  # a key the filter hands to the focused item itself
        self._selected: list[str] = []  # the selected views, in selection order
        self._hovered = ""  # the view under the pointer: the status bar speaks for it
        if hosts is not None:
            # a file opened from the file tree reuses a selected preview first
            # (newest pick first), else the focused one
            hosts.ctx.selected_views = lambda: [
                *reversed(self._selected),
                *([self._focused] if self._focused else []),
            ]
            # a file whose dedicated view is open already (#43): the view is selected,
            # panned into the viewport and focused instead of being opened again
            hosts.ctx.reveal_view = self.reveal
        self._nav_timer = QTimer(self)
        self._nav_timer.setSingleShot(True)
        self._nav_timer.setInterval(NAV_SETTLE_MS)
        self._nav_timer.timeout.connect(self.flush_navigation)
        self._off_patch = self.live.on_patch(self._on_patch)
        # the scratch pad's steps share Ctrl+Z with the canvas's (#184); ``_redo_order``
        # remembers which stack each undo took, so redo walks back the same way
        self.scratch = engine.scratch
        self._redo_order: list[str] = []
        self._off_scratch = self.scratch.on_change(self._on_scratch)
        # the project node's count follows the project intaker (the link into a file tree)
        self._off_events = self.live.events.subscribe(self._on_event)
        self.wiresChanged.connect(self._nodes_dirty)
        self.viewsChanged.connect(self._nodes_dirty)
        self.panChanged.connect(self.nodesChanged)  # the dots stay inside the viewport
        if hosts is not None:
            hosts.healthChanged.connect(self._on_health)
            hosts.hostCreated.connect(self._on_host_created)
        self.rebuild()

    def close(self) -> None:
        self._nav_timer.stop()
        self._off_patch()
        self._off_events()
        self._off_scratch()

    def _on_scratch(self, step: Any, kind: str) -> None:
        if kind not in ("undo", "redo"):
            self._redo_order = [s for s in self._redo_order if s != "scratch"]
        self.statusChanged.emit()

    def _on_event(self, ev: Any) -> None:
        if ev.kind != "link.resolved":
            return
        target = (ev.payload or {}).get("target") or {}
        view = self.data["views"].get(target.get("view"))
        if view is not None and view.get("type") == "file_tree":
            self.projectNodeChanged.emit()

    # -- the project source: a fixed node at the canvas origin (#83) -------------------------
    @Property("QVariantMap", notify=projectNodeChanged)
    def projectNode(self) -> dict[str, Any]:
        """The project source as the canvas draws it: its rect (canvas px, fixed at the
        origin), the identicon its tile shows (seeded "root", square: the node has no
        port of its own, #127), its refresh circle's centre (canvas px) and its tip, what
        its output carries (``all files`` and the count once the project intaker has
        landed)."""
        x, y, w, h = PROJECT_NODE
        try:
            files = self.engine.links.project_files
        except Exception:  # the runtime is lazily built; never let that block the canvas
            files = None
        count = len(files.paths) if isinstance(files, FilesValue) and files.paths else 0
        return {
            "id": PROJECT_ID,
            "port": PROJECT_PORT,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "icon": identicon_url(ROOT_SEED, None),
            "refresh": dict(zip("xy", project_refresh_anchor(), strict=True)),
            "count": count,
            "tip": project_tip(count if files else None),
            "label": self.canvasLabel or "project",
        }

    # -- document access -----------------------------------------------------------------
    @property
    def data(self) -> dict[str, Any]:
        return self.live.data

    def rect_of(self, view_id: str) -> Rect:
        """A view's rect in canvas px; a floating view's is the canvas rect under it at
        the current pan (so wires, hover and culling treat it like any view there). A
        collapsed view's is its title bar (#114): snapping, the marquee, hover and the
        ports use it; its stored size waits for the expand."""
        lay = self.data["views"][view_id].get("layout", {})
        (x, y), (w, h) = lay.get("position", (0, 0)), lay.get("size", (400, 300))
        if lay.get("collapsed"):
            h = COLLAPSED_H
        if lay.get("floating"):
            fx, fy = self.window_pos(view_id)
            return (self._pan[0] + fx, self._pan[1] + fy, float(w), float(h))
        return (float(x), float(y), float(w), float(h))

    def shown_rect(self, view_id: str) -> tuple[Rect, str]:
        """The rect a view is drawn at and its space, for the tile floor's cues (#208):
        a floating view's window rect in the canvas area's px (``"screen"``: a pan
        leaves it put), any other's :meth:`rect_of` in canvas px (``"world"``); a
        collapsed view's is its title bar either way."""
        x, y, w, h = self.rect_of(view_id)
        if self._is_floating(view_id):
            fx, fy = self.window_pos(view_id)
            return ((PAD + fx, PAD + fy, w, h), "screen")
        return ((x, y, w, h), "world")

    # -- the float layer (#112, ADR 0021) ------------------------------------------------------
    def window_size(self) -> tuple[float, float]:
        """The window frame's size: the canvas area inside its padding once the area has
        reported it (live while the window is being resized), else the document's."""
        if self._area[0] > 0 and self._area[1] > 0:
            return (self._area[0] - 2 * PAD, self._area[1] - 2 * PAD)
        _, _, w, h = self.frame_rect()
        return (w, h)

    def window_pos(self, view_id: str) -> tuple[float, float]:
        """A floating view's top-left in window px (the area's padded corner is 0, 0); a
        collapsed one is placed by its title bar (#116)."""
        lay = self.data["views"][view_id]["layout"]
        return window_position(lay["floating"], shown_size(lay), self.window_size())

    def floating_ids(self) -> list[str]:
        return [vid for vid, v in self.data["views"].items() if is_floating(v)]

    def _canvas_rects(self, exclude: list[str] | tuple[str, ...] = ()) -> dict[str, Rect]:
        """The rects of the views on the canvas (the float layer left out): what a canvas
        view snaps against."""
        return {
            vid: self.rect_of(vid)
            for vid, v in self.data["views"].items()
            if not is_floating(v) and vid not in exclude
        }

    def _float_rects(self, exclude: str = "") -> dict[str, Rect]:
        """The other floating views in window px: what a floating view snaps against."""
        out: dict[str, Rect] = {}
        for vid in self.floating_ids():
            if vid != exclude:
                _, _, w, h = self.rect_of(vid)
                out[vid] = (*self.window_pos(vid), w, h)
        return out

    @Slot(str, result=bool)
    def toggleFloat(self, view_id: str) -> bool:
        """The header's Float / Unfloat (#112): one user submit. The pan settles into the
        window frame's origin first, so the view floats where it is and lands back on
        the canvas point under it."""
        view = self.data["views"].get(view_id)
        if view is None:
            return False
        self.flush_navigation()
        if is_floating(view):
            return self.gesture(lambda: unfloat_view(self.live, view_id), "unfloat view")
        return self.gesture(lambda: float_view(self.live, view_id), "float view")

    def _is_collapsed(self, view_id: str) -> bool:
        return bool((self.data["views"].get(view_id) or {}).get("layout", {}).get("collapsed"))

    @Slot(str, result=bool)
    def toggleCollapse(self, view_id: str) -> bool:
        """The header's collapse / expand (#114): one user submit."""
        if view_id not in self.data["views"]:
            return False
        flag = not self._is_collapsed(view_id)
        what = "collapse view" if flag else "expand view"
        return self.gesture(lambda: set_collapsed(self.live, view_id, flag), what)

    def rects(self) -> dict[str, Rect]:
        return {vid: self.rect_of(vid) for vid in self.data["views"]}

    def live_rects(self) -> dict[str, Rect]:
        """The document rects with the gestures in progress applied (what is drawn)."""
        out = self.rects()
        for vid, r in self._drag_rects.items():
            if vid in out:
                out[vid] = r
        return out

    def set_drag_rect(self, view_id: str, rect: Rect | None) -> None:
        """A view follows ``rect`` while its gesture runs (None: the gesture ended); the
        wires move with it."""
        if rect is None:
            if self._drag_rects.pop(view_id, None) is None:
                return
        else:
            x, y, w, h = (float(v) for v in rect)
            if self._drag_rects.get(view_id) == (x, y, w, h):
                return
            self._drag_rects[view_id] = (x, y, w, h)
        self.dragRectsChanged.emit()
        self._rebuild_wires()

    @Property("QVariantMap", notify=dragRectsChanged)
    def dragRects(self) -> dict[str, dict[str, float]]:
        """The rects the views mid-gesture follow (canvas px): the cards of a group move
        bind to theirs while another view's header is dragged."""
        return {
            vid: {"x": x, "y": y, "w": w, "h": h} for vid, (x, y, w, h) in self._drag_rects.items()
        }

    @Slot(str)
    def cancelDrag(self, view_id: str) -> None:
        """The gesture ended without a drop (or after one): the wires settle on the
        document again, the group it carried too."""
        for vid in self._move_group(view_id):
            self.set_drag_rect(vid, None)

    def _move_group(self, view_id: str) -> list[str]:
        """The views a header drag on ``view_id`` moves (#84): the selection when it is
        part of one, else the view alone. The press settles the selection, so the group
        is the same from the first drag step to the drop."""
        if view_id in self._selected and not self._is_floating(view_id):
            return [vid for vid in self._selected if not self._is_floating(vid)]
        return [view_id]

    def _is_floating(self, view_id: str) -> bool:
        return is_floating(self.data["views"].get(view_id) or {})

    def frame_rect(self) -> Rect:
        r = self.data["window_frame"]["rect"]
        return (float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"]))

    # -- patches -------------------------------------------------------------------------
    def _on_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        if meta.get("kind") == "submit":  # a new step drops the canvas's redo tail
            self._redo_order = [s for s in self._redo_order if s != "canvas"]
        self.rebuild()
        self.patched.emit(str(meta.get("kind")))

    @Slot(str)
    def _on_health(self, view_id: str) -> None:
        self._rebuild_views()

    @Slot(str)
    def _on_host_created(self, view_id: str) -> None:
        self._recull()

    def rebuild(self) -> None:
        self._rebuild_view_ids()
        self._rebuild_wires()
        self._recull(rebuild=False)
        self._rebuild_views()
        self.statusChanged.emit()
        self.projectNodeChanged.emit()

    def set_all_paused(self, flag: bool) -> None:
        """The overworld owns rendering while it shows: every host pauses; back in the
        normal view the viewport decides again."""
        if flag != self._all_paused:
            self._all_paused = flag
            self._recull()
            if flag:
                self._set_hovered("")  # the overworld covers the views
            self.hintChanged.emit()  # the status line is the normal view's

    # -- pan and viewport ----------------------------------------------------------------------
    @Property("QVariantMap", notify=panChanged)
    def pan(self) -> dict[str, float]:
        return {"x": self._pan[0], "y": self._pan[1]}

    @Property("QVariantMap", notify=panChanged)
    def viewport(self) -> dict[str, float]:
        """The canvas area as a canvas rect (what the user sees; the prompt bar sends it
        with every request)."""
        x, y, w, h = self.viewport_rect()
        return {"x": x, "y": y, "w": w, "h": h}

    def viewport_rect(self) -> Rect:
        return (self._pan[0] - PAD, self._pan[1] - PAD, self._area[0], self._area[1])

    @Slot(float, float)
    def setViewportSize(self, w: float, h: float) -> None:
        size = (float(w), float(h))
        if size != self._area:
            self._area = size
            self.panChanged.emit()
            self._recull()
            if self.floating_ids():  # floaters keep their corner offsets
                self._rebuild_wires()
                self._rebuild_views()

    @Slot(float, float)
    def setViewportOrigin(self, x: float, y: float) -> None:
        """Where the canvas area sits in the window (so a window pointer position maps
        to canvas px)."""
        self._origin = (float(x), float(y))

    def frame_view_rect(self) -> Rect:
        """The window frame at the pan: the canvas rect that renders (the area inside its
        padding), by which views are culled and the quota counts."""
        _, _, w, h = self.frame_rect()
        return (self._pan[0], self._pan[1], w, h)

    @Slot(float, float)
    def setPan(self, x: float, y: float) -> None:
        pan = (float(round(x)), float(round(y)))
        if pan != self._pan:
            self._pan = pan
            self.panChanged.emit()
            self._recull()
            if self.floating_ids():
                self._rebuild_wires()  # the canvas moved under the float layer
            self._nav_timer.start()

    @Slot()
    def flush_navigation(self) -> None:
        """The pan settled: the window frame's origin follows it (no undo step)."""
        self._nav_timer.stop()
        self.live.navigate(self._pan)

    # -- maximise (story 72) ------------------------------------------------------------------
    @Property(str, notify=maximizedChanged)
    def maximized(self) -> str:
        return self._maximized

    @Slot(str)
    def maximize(self, view_id: str) -> None:
        """Fill the area with one view; the same id again, or "" (Esc), restores the
        layout. Nothing in the document changes."""
        vid = view_id if view_id in self.data["views"] and view_id != self._maximized else ""
        if vid == self._maximized:
            return
        self._maximized = vid
        self.maximizedChanged.emit()
        self._recull()

    @Slot(float, float)
    def panBy(self, dx: float, dy: float) -> None:
        self.setPan(self._pan[0] + dx, self._pan[1] + dy)

    # -- pan mode: Space held ---------------------------------------------------------------
    @Property(bool, notify=spaceHeldChanged)
    def spaceHeld(self) -> bool:
        return self._space_held

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Installed on the window; the key rule of the normal view (#113):

        - Space is the canvas's, outside a text field (which types it). Going down it
          arms the pan (the canvas area's hand layer takes the drags) and no view hears
          it; coming up it disarms. Released with no button pressed in between (a tap),
          it goes to the focused view as one press and release, so a gallery's quick
          look, a video's play / pause, an image's or a pdf's step keep their Space; a
          press-and-drag only pans. Losing the window drops the mode so it cannot stick.
        - Home and End go to the focused item first (image, video, pdf, usd, gallery
          jump to their ends; a text field moves its caret); a Home nothing takes pans
          the canvas home (``home``).
        - The backtick (tilde with Shift) toggles the wiring layer and is consumed,
          unless a text field has the focus, where it types. While the layer shows, Esc
          is the layer's (``wiringEscape``: it closes its popovers first, then leaves)
          whatever item has the focus: taken at the shortcut-override step so the
          window's Escape shortcut and the focused view's own Escape stay out of it."""
        t = event.type()
        if t == QEvent.Type.ShortcutOverride:
            if event.key() == Qt.Key.Key_Escape and self._wiring_owns_keys(obj):
                event.accept()
                return True
        elif t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if self._forwarding:
                return False
            if event.key() == Qt.Key.Key_Space:
                return self._space_key(obj, event)
            if event.key() == Qt.Key.Key_Home and t == QEvent.Type.KeyPress:
                if not self._forward_key(obj, event):
                    self.home()
                return True
            if event.isAutoRepeat():
                return False
            if event.key() in WIRING_KEYS and not _editing_text(obj):
                if t == QEvent.Type.KeyPress:
                    self.toggleWiring()
                return True
            elif event.key() == Qt.Key.Key_Escape and self._wiring_owns_keys(obj):
                if t == QEvent.Type.KeyPress:
                    self.wiringEscape.emit()
                return True
        elif t in (QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate):
            self._set_space_held(False)
        elif t == QEvent.Type.MouseButtonPress:
            self._space_pressed = self._space_pressed or self._space_held
        elif t == QEvent.Type.MouseMove:
            pos = event.position()
            self.hoverAt(pos.x(), pos.y())
        elif t == QEvent.Type.Leave:
            self._set_hovered("")
        return False

    def hoverAt(self, wx: float, wy: float) -> None:
        """The pointer is at a window position: the view under it is the hovered one
        (the focused view wins where views overlap, a maximised view takes the whole
        area, the overworld none)."""
        (ox, oy), (aw, ah) = self._origin, self._area
        if self._all_paused or not (ox <= wx < ox + aw and oy <= wy < oy + ah):
            self._set_hovered("")
            return
        if self._maximized:
            self._set_hovered(self._maximized)
            return
        cx, cy = wx - ox - PAD + self._pan[0], wy - oy - PAD + self._pan[1]
        hits = [
            vid
            for vid, (x, y, w, h) in self.live_rects().items()
            if vid not in self._offscreen and x <= cx < x + w and y <= cy < y + h
        ]
        # the float layer lies above the canvas: a floater under the pointer wins
        on_top = [vid for vid in hits if self._is_floating(vid)] or hits
        self._set_hovered(
            self._focused if self._focused in on_top else (on_top[-1] if on_top else "")
        )

    def _wiring_owns_keys(self, window: QObject) -> bool:
        """The wiring layer shows (not hidden under the overworld) and no text field
        has the focus."""
        return self._wiring and not self._all_paused and not _editing_text(window)

    # -- the wiring layer (#82) ------------------------------------------------------------------
    @Property(bool, notify=wiringChanged)
    def wiring(self) -> bool:
        return self._wiring

    @Slot(bool)
    def setWiring(self, on: bool) -> None:
        """Show or leave the wiring layer: navigation state, no undo step."""
        if bool(on) != self._wiring:
            self._wiring = bool(on)
            self.wiringChanged.emit()
            self.hintChanged.emit()

    @Slot()
    def toggleWiring(self) -> None:
        self.setWiring(not self._wiring)

    def _space_key(self, window: QObject, event: QKeyEvent) -> bool:
        """Space through the window (see ``eventFilter``); True when the canvas took it."""
        if event.type() == QEvent.Type.KeyPress:
            if event.isAutoRepeat() or _editing_text(window):
                return self._space_held  # held: the repeats are the canvas's too
            self._space_pressed = False
            self._set_space_held(True)
            return True
        if not self._space_held or event.isAutoRepeat():
            return self._space_held
        self._set_space_held(False)
        if not self._space_pressed:  # a tap: the focused view's Space
            self._forward_key(window, event)
        return True

    def _forward_key(self, window: QObject, event: QKeyEvent) -> bool:
        """Hand ``event``'s key to the window's focused item as a press and a release,
        past this filter; True when an item (or one of its parents) took the press."""
        args = (event.key(), event.modifiers(), event.text())
        press = QKeyEvent(QEvent.Type.KeyPress, *args)
        press.ignore()  # stays ignored when no item has the focus
        self._forwarding = True
        try:
            QCoreApplication.sendEvent(window, press)
            QCoreApplication.sendEvent(window, QKeyEvent(QEvent.Type.KeyRelease, *args))
        finally:
            self._forwarding = False
        return press.isAccepted()

    def _set_space_held(self, held: bool) -> None:
        if held != self._space_held:
            self._space_held = held
            self.spaceHeldChanged.emit()

    # -- selected views (navigation state, no undo step) ---------------------------------------
    @Property("QVariantList", notify=selectionChanged)
    def selectedViews(self) -> list[str]:
        return list(self._selected)

    def _set_selection(self, ids: list[str]) -> None:
        ids = [vid for vid in dict.fromkeys(ids) if vid in self._views]
        if ids == self._selected:
            return
        self._selected = ids
        for vid, facts in self._views.items():
            facts["selected"] = vid in ids
        self.selectionChanged.emit()
        self.viewsChanged.emit()
        self.hintChanged.emit()

    # -- hover: the status bar speaks for the view under the pointer ---------------------------
    def _set_hovered(self, view_id: str) -> None:
        if view_id not in self._views:
            view_id = ""
        if view_id != self._hovered:
            self._hovered = view_id
            self.hintChanged.emit()

    @Slot(str)
    def hoverView(self, view_id: str) -> None:
        """Name the hovered view directly (tools, tests; the pointer normally decides)."""
        self._set_hovered(view_id)

    @Slot(str)
    def leaveView(self, view_id: str) -> None:
        """The pointer left that view (a leave after another view's enter is ignored)."""
        if view_id == self._hovered:
            self._set_hovered("")

    @Property(str, notify=hintChanged)
    def hoveredView(self) -> str:
        return self._hovered

    @Property(str, notify=hintChanged)
    def statusHint(self) -> str:
        """The status bar's line: the wiring layer's while it shows (#82), else the
        hovered view's hints, else the last selected view's; empty when neither has
        anything to say, and while the overworld shows (every host is paused there,
        which is no news about the view)."""
        if self._all_paused:
            return ""
        if self._wiring:
            return WIRING_HINT
        if self._hovered:
            return status_hint(self._views[self._hovered], hovered=True)
        if self._selected:
            return status_hint(self._views[self._selected[-1]], hovered=False)
        return ""

    @Slot(str, bool)
    def selectView(self, view_id: str, toggle: bool) -> None:
        """A click on a view's header: the selection becomes this view; with Shift or
        Ctrl (``toggle``) the view joins or leaves it."""
        if toggle:
            if view_id in self._selected:
                self._set_selection([vid for vid in self._selected if vid != view_id])
            else:
                self._set_selection([*self._selected, view_id])
        else:
            self._set_selection([view_id])

    @Slot(str, bool)
    def pressView(self, view_id: str, toggle: bool) -> None:
        """The press that may start a header drag: a view already selected keeps the
        selection it is part of (the release collapses it when no drag followed)."""
        if toggle or view_id not in self._selected:
            self.selectView(view_id, toggle)

    @Slot("QVariantList")
    def setSelection(self, view_ids: list) -> None:
        self._set_selection([str(v) for v in view_ids])

    @Slot()
    def clearSelection(self) -> None:
        self._set_selection([])

    @Slot(float, float, float, float, "QVariantList")
    def selectInRect(self, x: float, y: float, w: float, h: float, base: list) -> None:
        """The marquee (canvas px): the views whose rect it touches, on top of ``base``
        (the selection before an additive drag; empty for a plain one)."""
        hits = [
            vid
            for vid, (vx, vy, vw, vh) in self.rects().items()
            if x < vx + vw and vx < x + w and y < vy + vh and vy < y + h
        ]
        self._set_selection([*(str(v) for v in base), *hits])

    @Slot()
    def home(self) -> None:
        """Home: the project node (fixed at the canvas origin) and the file tree back
        into view, their top-left corner at the area's padded corner, unless both are
        already fully on screen."""
        rects = [PROJECT_NODE]
        tree = next(
            (vid for vid, v in self.data["views"].items() if v.get("type") == "file_tree"), None
        )
        if tree is not None:
            rects.append(self.rect_of(tree))
        x = min(r[0] for r in rects)
        y = min(r[1] for r in rects)
        vx, vy, vw, vh = self.viewport_rect()
        if all(
            vx <= rx and vy <= ry and rx + rw <= vx + vw and ry + rh <= vy + vh
            for rx, ry, rw, rh in rects
        ):
            return
        self.setPan(x, y)

    def _recull(self, *, rebuild: bool = True) -> None:
        """Pause the hosts of views outside the viewport, resume the ones back inside;
        slots follow through ``views[*].onScreen``."""
        if self._area == (0.0, 0.0):
            return  # the area has not reported its size yet: nothing is off screen
        if self._maximized and self._maximized not in self.data["views"]:
            self._maximized = ""
            self.maximizedChanged.emit()
        if self._all_paused:
            on = set()
        elif self._maximized:
            on = {self._maximized}
        else:
            # the float layer is always on screen (#112): panning never culls it
            on = visible_ids(self.frame_view_rect(), self.rects()) | set(self.floating_ids())
        off = set(self.data["views"]) - on
        # a collapsed view stays drawn (its title bar) but its content pauses (#114)
        folded = {vid for vid in on if self._is_collapsed(vid) and vid != self._maximized}
        changed = off != self._offscreen
        self._offscreen = off
        if self.hosts is not None:
            for vid in off:
                self.hosts.pause(vid)
            for vid in folded:
                self.hosts.pause(vid, COLLAPSED)
            for vid in on - folded:
                self.hosts.resume(vid)
        if changed and rebuild:
            self._rebuild_views()

    def _rebuild_view_ids(self) -> None:
        ids = []
        for vid, v in self.data["views"].items():
            lay = v.get("layout", {})
            (x, y), (w, h) = lay.get("position", (0, 0)), lay.get("size", (400, 300))
            ids.append({"id": vid, "x": float(x), "y": float(y), "w": float(w), "h": float(h)})
        if ids != self._view_ids:
            self._view_ids = ids
            self._view_list.set_ids([e["id"] for e in ids])
            self.viewIdsChanged.emit()

    def _rebuild_wires(self) -> None:
        """One curve per link with its chain colour and summary; the chain tokens of the
        views come from the same pass so both agree."""
        links = self.data["links"]
        self._chain_nums = chains_of(self.data["views"], links)
        self._chains = chain_tokens(self._chain_nums)
        wires = []
        for w in wire_geometry(self.live_rects(), links):
            link = links[w["id"]]
            wires.append(
                {
                    **w,
                    "kind": link.get("kind", ""),
                    "label": link.get("label", ""),
                    "sentence": link.get("summary", {}).get("sentence", ""),
                    # the link node's tip (#121): its ends and what flows
                    "tip": link_tip(self.data, w["id"]),
                    "twoWay": bool(link.get("two_way")),
                    "locked": bool(link.get("locked")),
                    "chain": self._chains.get(w["target"], ""),
                }
            )
        if wires != self._wires:
            self._wires = wires
            self.wiresChanged.emit()
        ids = [w["id"] for w in wires]
        if ids != self._wire_ids:
            self._wire_ids = ids
            self.wireIdsChanged.emit()
        self._rebuild_capsules()

    def _rebuild_capsules(self) -> None:
        """The port pairs (#160) at the drawn rects, each with its midpoint, whether a
        capsule joins it and its chain's colour token."""
        pairs = port_pairs(self.live_rects(), self.data["links"])
        self._capsules.update(pairs)
        joined = self._capsules.joined
        cmap = {
            p["id"]: {
                **p,
                "mx": (p["x1"] + p["x2"]) / 2,
                "my": (p["y1"] + p["y2"]) / 2,
                "joined": p["id"] in joined,
                "chain": self._chains.get(p["inView"], ""),
            }
            for p in pairs
        }
        if cmap != self._capsule_map:
            self._capsule_map = cmap
            self.capsulesChanged.emit()
        ids = [p["id"] for p in pairs]
        if ids != self._capsule_ids:
            self._capsule_ids = ids
            self.capsuleIdsChanged.emit()

    def _rebuild_views(self) -> None:
        out: dict[str, dict[str, Any]] = {}
        # views the user left unreachable from the project source say so (#83): that
        # outranks what their host reports, until a link reaches them again
        unconnected = unreachable_views(self.data)
        for vid, v in self.data["views"].items():
            health = self.hosts.ctx.health_of(vid) if self.hosts is not None else {}
            if vid in unconnected:
                health = {
                    "status": DISCONNECTED,
                    "message": DISCONNECTED_MESSAGE,
                    "hint": DISCONNECTED_HINT,
                }
            paused = self.hosts is not None and self.hosts.is_paused(vid)
            waiting = self.hosts.approval_pending(vid) if self.hosts is not None else []
            x, y, w, h = self.rect_of(vid)
            fx, fy = self.window_pos(vid) if is_floating(v) else (0.0, 0.0)
            host = self.hosts.hosts.get(vid) if self.hosts is not None else None
            out[vid] = {
                "id": vid,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "label": v.get("label") or vid,
                "type": v.get("type", ""),
                # the float layer (#112): fixed in the window at (fx, fy) window px;
                # floatBy says who floated it (the user's binds the agent)
                "floating": is_floating(v),
                "floatBy": (v["layout"].get("floating") or {}).get("by", ""),
                "fx": fx,
                "fy": fy,
                # the title bar alone (#114): rect h is the bar's, the size waits
                "collapsed": bool(v["layout"].get("collapsed")),
                # whose step keys the status bar names: a preview's renderer (#106)
                "stepType": getattr(host, "step_type", None) or v.get("type", ""),
                # the host's own gesture line for its state (#39), else the type's
                "keys": getattr(host, "nav_keys", "") or "",
                "locked": bool(v.get("locked")),
                # the contextual view (#43): the preview on a selection, tagged in its
                # header and on its overworld card so it is not taken for a dedicated one
                "contextual": is_contextual(self.data, vid),
                # the header's auto-update switch (#102): None when no intaker feeds it,
                # or the view follows what it is fed (#126: the preview)
                "autoUpdate": None
                if v.get("type") in NO_AUTO_UPDATE
                else auto_update_of(self.data, vid),
                "health": health.get("status", "ok"),
                "healthMessage": health.get("message", ""),
                # the empty-state line shown in the view's area (nothing picked yet)
                "hint": health.get("hint", ""),
                "paused": paused,
                # the Python link file(s) into this view awaiting the user's approval (#43)
                "approval": ", ".join(str(st.get("path", lid)) for lid, st in waiting),
                "onScreen": vid not in self._offscreen,
                "selected": vid in self._selected,
                "chain": self._chains.get(vid, ""),
                "inputs": [
                    lid for lid, ln in self.data["links"].items() if ln["target"]["view"] == vid
                ],
                "outputs": [
                    lid for lid, ln in self.data["links"].items() if ln["source"]["view"] == vid
                ],
                # the port circles (#81): one input circle per linked input, each with
                # the upstream view's identicon; the output circle with the view's own
                "inPorts": [
                    {**p, "icon": self.identicon_of(p["from"])} for p in input_ports(self.data, vid)
                ],
                "inTip": unlinked_input_tip(self.data, vid),
                "icon": self.identicon_of(vid),
                "outLinked": output_linked(self.data, vid),
                "outTip": output_tip(self.data, vid),
            }
        self._views = out
        if self._hovered and self._hovered not in out:
            self._hovered = ""
        if self._focused and self._focused not in out:
            self._focused = ""
            self.focusChanged.emit()
        if any(vid not in out for vid in self._selected):
            self._selected = [vid for vid in self._selected if vid in out]
            self.selectionChanged.emit()
        self.viewsChanged.emit()
        self.hintChanged.emit()

    # -- QML models ------------------------------------------------------------------------
    @Property(str, notify=statusChanged)
    def canvasLabel(self) -> str:
        return self.data.get("project", {}).get("label", "")

    @Property("QVariantList", notify=viewIdsChanged)
    def viewIds(self) -> list[dict[str, Any]]:
        return [dict(e) for e in self._view_ids]

    @Property(QObject, constant=True)
    def viewList(self) -> ViewList:
        return self._view_list

    @Property("QVariantMap", notify=viewsChanged)
    def views(self) -> dict[str, dict[str, Any]]:
        return dict(self._views)

    @Property("QVariantList", notify=wiresChanged)
    def wires(self) -> list[dict[str, Any]]:
        """The links as curves in canvas px: ``{id, source, target, x1, y1, x2, y2,
        orient, nx, ny, kind, label, sentence, tip, twoWay, locked, chain}``."""
        return [dict(w) for w in self._wires]

    @Property("QVariantList", notify=wireIdsChanged)
    def wireIds(self) -> list[str]:
        """The link ids with a curve, in document order: changes only when links come
        or go, so one curve item per link survives a drag."""
        return list(self._wire_ids)

    @Property("QVariantMap", notify=wiresChanged)
    def wireMap(self) -> dict[str, dict[str, Any]]:
        return {w["id"]: dict(w) for w in self._wires}

    @Property("QVariantList", notify=capsuleIdsChanged)
    def capsuleIds(self) -> list[str]:
        """The port pairs (#160) in document order: changes only when links come or go,
        so one capsule item per pair survives a drag (and animates its join and split)."""
        return list(self._capsule_ids)

    @Property("QVariantMap", notify=capsulesChanged)
    def capsuleMap(self) -> dict[str, dict[str, Any]]:
        """``pair id -> {id, link, outView, inView, inIndex, x1, y1, x2, y2, mx, my,
        joined, chain}``: the output and input circles' centres and their midpoint in
        canvas px (``canvas.capsules``), whether a capsule joins them now."""
        return {k: dict(v) for k, v in self._capsule_map.items()}

    # -- link nodes (#90) ----------------------------------------------------------------
    def place_nodes(
        self, placer: NodePlacer, bounds: Rect | None = None
    ) -> dict[str, dict[str, float]]:
        """``placer``'s dot places over the current wires, rects and input circles,
        inside ``bounds`` (canvas px) when given."""
        inputs = {vid: len(v.get("inPorts", [])) for vid, v in self._views.items()}
        return placer.places(self._wires, self.live_rects(), inputs, bounds)

    @Slot()
    def _nodes_dirty(self) -> None:
        self._nodes.invalidate()
        self.nodesChanged.emit()

    @Slot(str, float, float)
    def setNodeSize(self, link_id: str, w: float, h: float) -> None:
        """A wiring-layer link node's chip is ``w`` x ``h`` px (the QML reports it)."""
        if self._nodes.set_size(link_id, w, h):
            self.nodesChanged.emit()

    @Property("QVariantMap", notify=nodesChanged)
    def nodePlaces(self) -> dict[str, dict[str, float]]:
        """``link id -> {x, y}``: the wiring layer's link dot centres in canvas px,
        inside the canvas area the user sees (#121: no top bar above it any more)."""
        return self.place_nodes(self._nodes, self.viewport_rect())

    def chain_of(self, view_id: str) -> str:
        return self._chains.get(view_id, "")

    def identicon_of(self, view_id: str) -> str:
        """The round identicon URL a port circle shows for a view: its grid from its
        name, its colour from its chain on this canvas. Hubs are neutral wherever their
        identicon shows, on their own port and on every input they feed (user decision
        on #83): the project source (its grid seeded with ``ROOT_SEED``; its id stays
        ``project``) and file trees, which feed many chains. Ordinary views keep their
        chain's colour (#92)."""
        if view_id == PROJECT_ID:
            return identicon_url(ROOT_SEED, None, round=True)
        if self.data["views"].get(view_id, {}).get("type") == "file_tree":
            return identicon_url(view_id, None, round=True)
        return identicon_url(view_id, self._chain_nums.get(view_id), round=True)

    @Property("QVariantMap", notify=statusChanged)
    def windowFrame(self) -> dict[str, float]:
        return dict(self.data["window_frame"]["rect"])

    @Property(int, notify=viewsChanged)
    def viewCount(self) -> int:
        return len(self._views)

    @Property(int, notify=viewsChanged)
    def pausedCount(self) -> int:
        return sum(1 for v in self._views.values() if v.get("paused"))

    @Property(int, notify=viewsChanged)
    def resolvingCount(self) -> int:
        return sum(1 for v in self._views.values() if v.get("health") == "loading")

    @Property(str, notify=focusChanged)
    def focusedView(self) -> str:
        return self._focused

    @Slot(str, result="QVariant")
    def view(self, view_id: str) -> Any:
        return self._views.get(view_id) or {}

    # -- undo ----------------------------------------------------------------------------------
    # One Ctrl+Z for two stacks (#184): the canvas's submits and the scratch pad's steps.
    # Undo takes the stack whose top step is the newer; redo the stack the latest undo
    # took. ``undoCursor`` and ``undoDepth`` stay the canvas's.
    @Property(int, notify=statusChanged)
    def undoCursor(self) -> int:
        return self.live.cursor

    @Property(int, notify=statusChanged)
    def undoDepth(self) -> int:
        return len(self.live.history)

    def _undo_stack(self) -> str:
        """``canvas`` or ``scratch``: the newer of the two top steps; "" for none."""
        canvas = self.live.history[self.live.cursor - 1] if self.live.cursor else None
        scratch = self.scratch.top
        if scratch is not None and (canvas is None or scratch.ts > canvas.ts):
            return "scratch"
        return "canvas" if canvas is not None else ""

    def _redo_stack(self) -> str:
        can = {
            "canvas": self.live.cursor < len(self.live.history),
            "scratch": self.scratch.next_redo is not None,
        }
        for stack in reversed(self._redo_order):
            if can[stack]:
                return stack
        return next((s for s in ("canvas", "scratch") if can[s]), "")

    @Property(bool, notify=statusChanged)
    def canUndo(self) -> bool:
        return bool(self._undo_stack())

    @Property(bool, notify=statusChanged)
    def canRedo(self) -> bool:
        return bool(self._redo_stack())

    @Property(str, notify=statusChanged)
    def lastTxnLabel(self) -> str:
        stack = self._undo_stack()
        if stack == "scratch":
            s = self.scratch.top
            return f"{s.id} scratch pad: {s.label} ({s.origin})"
        if not stack:
            return ""
        r = self.live.history[self.live.cursor - 1]
        return f"{r.id} {r.label} ({r.origin})"

    @Property(str, notify=statusChanged)
    def lastTxnId(self) -> str:
        return self.live.top_id or ""

    @Property(str, notify=statusChanged)
    def nextRedoLabel(self) -> str:
        stack = self._redo_stack()
        if stack == "scratch":
            s = self.scratch.next_redo
            return f"{s.id} scratch pad: {s.label} ({s.origin})"
        if not stack:
            return ""
        r = self.live.history[self.live.cursor]
        return f"{r.id} {r.label} ({r.origin})"

    @Slot(result=bool)
    def undo(self) -> bool:
        stack = self._undo_stack()
        if not stack:
            if self.notices is not None:
                self.notices.info("nothing to undo")
            return False
        if stack == "scratch":
            ok = self.gesture(self.scratch.undo, "undo")
        else:
            ok = self.gesture(lambda: self.live.undo(origin="user"), "undo")
        if ok:
            self._redo_order.append(stack)
        return ok

    @Slot(result=bool)
    def redo(self) -> bool:
        stack = self._redo_stack()
        if not stack:
            if self.notices is not None:
                self.notices.info("nothing to redo")
            return False
        if stack in self._redo_order:
            i = len(self._redo_order) - 1 - self._redo_order[::-1].index(stack)
            del self._redo_order[i]
        if stack == "scratch":
            return self.gesture(self.scratch.redo, "redo")
        return self.gesture(lambda: self.live.redo(origin="user"), "redo")

    # -- the scratch pad (#184) ------------------------------------------------------------------
    @Slot(result=str)
    def openScratch(self) -> str:
        """The project node's double-click: the scratch pad, added beside the file tree
        when the canvas has none (one user submit), then brought into view."""
        try:
            vid, _res = open_scratch(self.live)
        except SpectoError as e:
            if self.notices is not None:
                self.notices.error(e.message, fix=e.fix, code=e.code, command="open scratch pad")
            return ""
        if vid in self._views:
            self.jumpTo(vid)
        return vid

    # -- gestures (submits as the user) -----------------------------------------------------
    @Slot(str, result=bool)
    def removeView(self, view_id: str) -> bool:
        """The header's close button: the view and its links leave the document."""
        return self.gesture(lambda: close_view(self.live, view_id), "close view")

    @Slot(str, result=bool)
    def toggleLock(self, view_id: str) -> bool:
        """The header's lock (story 74): lock or unlock the view as the user, one step."""
        view = self.data["views"].get(view_id)
        if view is None:
            return False
        locked = not view.get("locked", False)
        what = "lock view" if locked else "unlock view"
        return self.gesture(lambda: set_locked(self.live, view_id, locked), what)

    @Slot(str, result=bool)
    def toggleAutoUpdate(self, view_id: str) -> bool:
        """The header's auto-update switch (#102): the view's intakers watch their sources,
        or stop; one user submit."""
        on = auto_update_of(self.data, view_id)
        if on is None:
            return False
        what = "auto-update on" if not on else "auto-update off"
        return self.gesture(lambda: set_auto_update(self.live, view_id, not on), what)

    @Slot(str, result=bool)
    def refreshView(self, view_id: str) -> bool:
        """The header's refresh (auto-update off, #102): the intakers feeding the view
        rescan once. Not an undo step: nothing in the document changes."""
        return self.gesture(lambda: self.engine.links.refresh(view_id), "refresh")

    @Slot(result=bool)
    def refreshProject(self) -> bool:
        """The project node's refresh (#102): every link off the project source (the file
        tree's intaker, the pinned ones) rescans once; only what changed is delivered."""
        return self.gesture(lambda: self.engine.links.refresh(), "refresh project")

    @Slot(str, result=bool)
    def approveView(self, view_id: str) -> bool:
        """The header's Approve button: the user approves the Python file of every input
        link of this view that awaits it (ADR 0006, by content hash); those links resolve.
        Not an undo step: trust is not part of the document."""
        if self.hosts is None:
            return False
        pending = [lid for lid, _ in self.hosts.approval_pending(view_id)]
        if not pending:
            if self.notices is not None:
                self.notices.info(f"{view_id}: nothing awaits approval")
            return False
        ok = True
        for lid in pending:
            ok = self.gesture(lambda lid=lid: self.engine.links.approve(lid), "approve") and ok
        if ok and self.notices is not None:
            self.notices.info(f"approved {', '.join(pending)}")
        self.rebuild()
        return ok

    @Property("QVariantList", notify=snapGuidesChanged)
    def snapGuides(self) -> list[dict[str, Any]]:
        """The borders the gesture in progress snapped to, as ``{axis, value}`` in canvas
        px; empty between gestures."""
        return [dict(g) for g in self._guides]

    def _set_guides(self, snapped: Snapped | None) -> None:
        guides = [{"axis": a, "value": v} for a, v in snapped.guides] if snapped else []
        if guides != self._guides:
            self._guides = guides
            self.snapGuidesChanged.emit()

    def snapped_move(self, view_id: str, x: float, y: float) -> Rect:
        """Where a view dropped at (x, y) lands once snapped (the overworld resolves its
        free positions through this)."""
        return self._snap_move(view_id, x, y).rect

    def snapped_resize(self, view_id: str, rect: Rect, edges: list[str]) -> Rect:
        return self._snap_resize(view_id, rect, edges).rect

    def _snap_move(self, view_id: str, x: float, y: float, group: list[str] = ()) -> Snapped:
        """Snap the dragged view against the views that stay put: the group moving with
        it offers no borders."""
        _, _, w, h = self.rect_of(view_id)
        if self._is_floating(view_id):
            return self._in_window(
                snap_move(
                    self._to_window((float(x), float(y), w, h)),
                    self._float_rects(view_id),
                    (0.0, 0.0, *self.window_size()),
                    threshold=SNAP_PX,
                )
            )
        return snap_move(
            (float(x), float(y), w, h),
            self._canvas_rects(group),
            self.frame_rect(),
            threshold=SNAP_PX,
            exclude=view_id,
        )

    def _to_window(self, rect: Rect) -> Rect:
        x, y, w, h = rect
        return (x - self._pan[0], y - self._pan[1], w, h)

    def _in_window(self, snapped: Snapped) -> Snapped:
        """A floater's snap, made in window px, back in canvas px at the pan (rect and
        guides), its rect kept inside the window."""
        px, py = self._pan
        x, y, w, h = snapped.rect
        ww, wh = self.window_size()
        x = min(max(x, 0.0), max(0.0, ww - w))
        y = min(max(y, 0.0), max(0.0, wh - h))
        guides = tuple((a, v + (px if a == "x" else py)) for a, v in snapped.guides)
        return Snapped((x + px, y + py, w, h), guides)

    def _group_positions(
        self, view_id: str, x: float, y: float, group: list[str]
    ) -> dict[str, tuple[float, float]]:
        """Where each view of the group lands when the dragged one is dropped at (x, y):
        the dragged view snapped, the rest shifted by its delta."""
        sx, sy, _, _ = self._snap_move(view_id, x, y, group).rect
        ox, oy, _, _ = self.rect_of(view_id)
        dx, dy = sx - ox, sy - oy
        out: dict[str, tuple[float, float]] = {}
        for vid in group:
            vx, vy, _, _ = self.rect_of(vid)
            out[vid] = (vx + dx, vy + dy)
        return out

    def _snap_resize(self, view_id: str, rect: Rect, edges: list[str]) -> Snapped:
        if self._is_floating(view_id):
            return self._in_window(
                snap_resize(
                    self._to_window(rect),
                    edges,
                    self._float_rects(view_id),
                    (0.0, 0.0, *self.window_size()),
                    threshold=SNAP_PX,
                )
            )
        return snap_resize(
            rect,
            edges,
            self._canvas_rects(),
            self.frame_rect(),
            threshold=SNAP_PX,
            exclude=view_id,
        )

    @staticmethod
    def _rect_map(snapped: Snapped) -> dict[str, float]:
        x, y, w, h = snapped.rect
        return {"x": x, "y": y, "w": w, "h": h}

    @Slot(str, float, float, result="QVariantMap")
    def dragMove(self, view_id: str, x: float, y: float) -> dict[str, float]:
        """A move in progress: the snapped position for the card to follow; the rest of
        its selection follows by the same delta (``dragRects``)."""
        if view_id not in self.data["views"]:
            return {}
        group = self._move_group(view_id)
        snapped = self._snap_move(view_id, x, y, group)
        self._set_guides(snapped)
        for vid, (vx, vy) in self._group_positions(view_id, x, y, group).items():
            _, _, w, h = self.rect_of(vid)
            self.set_drag_rect(vid, (vx, vy, w, h))
        return self._rect_map(snapped)

    @Slot(str, float, float, result=bool)
    def endMove(self, view_id: str, x: float, y: float) -> bool:
        """The drop: one user submit (``move <label>``, or ``move N views`` for a group),
        nothing when the view is back where it started."""
        self._set_guides(None)
        group = self._move_group(view_id)
        for vid in group:
            self.set_drag_rect(vid, None)
        if view_id not in self.data["views"]:
            return False
        if self._is_floating(view_id):
            sx, sy, _, _ = self._to_window(self._snap_move(view_id, x, y).rect)
            if (sx, sy) == self.window_pos(view_id):
                return False
            return self.gesture(lambda: place_floating(self.live, view_id, (sx, sy)), "move view")
        positions = self._group_positions(view_id, x, y, group)
        return self.gesture(lambda: move_views(self.live, positions), "move view")

    @Slot(str, float, float, float, float, "QVariantList", result="QVariantMap")
    def dragResize(
        self, view_id: str, x: float, y: float, w: float, h: float, edges: list[str]
    ) -> dict[str, float]:
        if view_id not in self.data["views"]:
            return {}
        snapped = self._snap_resize(view_id, (x, y, w, h), [str(e) for e in edges])
        self._set_guides(snapped)
        self.set_drag_rect(view_id, snapped.rect)
        return self._rect_map(snapped)

    @Slot(str, float, float, float, float, "QVariantList", result=bool)
    def endResize(
        self, view_id: str, x: float, y: float, w: float, h: float, edges: list[str]
    ) -> bool:
        """One user submit (``resize <label>``); a left or top edge moves the origin too.
        A collapsed view keeps its stored height."""
        self._set_guides(None)
        self.set_drag_rect(view_id, None)
        if view_id not in self.data["views"]:
            return False
        sx, sy, sw, sh = self._snap_resize(view_id, (x, y, w, h), [str(e) for e in edges]).rect
        if self._is_collapsed(view_id):
            # a title bar resizes its width; the height it expands to stays (#114)
            sh = float(self.data["views"][view_id]["layout"]["size"][1])
        if self._is_floating(view_id):
            fx, fy, _, _ = self._to_window((sx, sy, sw, sh))
            return self.gesture(
                lambda: place_floating(self.live, view_id, (fx, fy), size=(sw, sh)), "resize view"
            )
        return self.gesture(
            lambda: resize_view(self.live, view_id, (sw, sh), position=(sx, sy)), "resize view"
        )

    def gesture(self, fn: Any, what: str) -> bool:
        """Run a user gesture (a submit, undo, approve); a ``SpectoError`` becomes a
        notice naming ``what`` and False. The overworld's gestures go through here too."""
        try:
            fn()
        except SpectoError as e:
            if self.notices is not None:
                self.notices.error(e.message, fix=e.fix, code=e.code, command=what)
            else:
                print(f"[ERROR] {what}: {e}")
            return False
        return True

    @Slot(str, bool, result="QVariant")
    def snap(self, target: str, image: bool = False) -> Any:
        try:
            res = self.engine.call("snap", {"target": target or "canvas", "image": image})
        except SpectoError as e:
            if self.notices is not None:
                self.notices.error(e.message, fix=e.fix, code=e.code, command="snap")
            return None
        if self.notices is not None:
            where = f" -> {res['image']}" if res.get("image") else ""
            self.notices.info(f"snap {res['kind']} {res['target']}{where}")
        return res

    # -- focus / fly-to ----------------------------------------------------------------------
    @Slot(str)
    def focusView(self, view_id: str) -> None:
        if view_id != self._focused:
            self._focused = view_id if view_id in self._views else ""
            self.focusChanged.emit()

    def reveal(self, view_id: str) -> bool:
        """Bring a view to the user (#43, the hosts' ``ctx.reveal_view``): the selection
        becomes this view, the canvas pans until it is on screen, and it is focused."""
        if view_id not in self._views:
            return False
        self._set_selection([view_id])
        return self.jumpTo(view_id)

    @Slot(str, result=bool)
    def flyTo(self, view_id: str) -> bool:
        if view_id not in self._views:
            if self.notices is not None:
                self.notices.warn(f"{view_id} is no longer on the canvas")
            return False
        self.focusView(view_id)
        self.flash.emit(view_id)
        return True

    @Slot(str, result=bool)
    def jumpTo(self, view_id: str) -> bool:
        """A view's name clicked in a tip (#127): the canvas pans until the view is on
        screen (centred on each axis it fits, else its top-left at the padded corner),
        unless it already is, then flies to it. A floating view is always on screen. The
        project source's name goes Home and its node pulses."""
        if view_id == PROJECT_ID:
            self.home()
            self.flash.emit(PROJECT_ID)
            return True
        view = self.data["views"].get(view_id)
        if view is not None and not is_floating(view):
            x, y, w, h = self.rect_of(view_id)
            vx, vy, vw, vh = self.viewport_rect()
            if not (vx <= x and vy <= y and x + w <= vx + vw and y + h <= vy + vh):

                def axis(start: float, size: float, span: float) -> float:
                    room = span - 2 * PAD
                    return start + size / 2 - span / 2 + PAD if size <= room else start

                self.setPan(axis(x, w, vw), axis(y, h, vh))
        return self.flyTo(view_id)

    # -- misc ----------------------------------------------------------------------------------
    def summary(self, view_id: str) -> dict[str, Any]:
        v = self.data["views"].get(view_id, {})
        return {"id": view_id, **{k: v.get(k) for k in ("type", "label", "layout", "locked")}}


__all__ = ["CanvasController"]
