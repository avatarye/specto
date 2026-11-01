"""The overworld (#45; spec #35 stories 68-71; glossary: overworld, link node, link
summary, link view).

Tab toggles it. On the way in the shell grabs a snapshot of every on-screen view, each
item on its own (``canvas.snapshots.capture``); the grabs land a frame later, and once
they have (or after ``GRAB_WAIT_MS``, so a grab that never lands cannot block the way in)
the canvas controller pauses every host and the overworld shows the whole canvas zoomed
out as cards carrying those snapshots, with the links as clear chain-coloured wires and a
link node on each. Pausing after the grab keeps a host from blanking its own snapshot.
Moving and resizing a card is free while the pointer is down and resolves snapped
(``canvas.snapping``, through the controller) into the normal view on release, as one
user submit, so both levels show one layout. A link node (a dot, #121) opens the
link view (``linkDetails``): Delete (one user submit) and Ask agent to change (pre-fills
the prompt bar through ``askAgentRequested``).
On the way out the viewport decides again which hosts render.

Cards follow the normal view's rules (#90): ``name: label``, the health dot and the lock
in the header, no footer, and the #81 port circles (``inPorts`` with the upstream view's
identicon, ``icon`` the view's own). A link node never covers a card's header: the QML
reports each chip's canvas size (``setNodeSize``) and the screen scale
(``setScreenScale``), and ``nodePlaces`` puts every node on its wire clear of the cards,
else clear of their headers, else nudged off them, and never on a port circle
(``chains.NodePlacer``, the wiring layer's rule too).

Floating views (#112, ADR 0021) are not on the canvas: they show in a small "floating"
strip along the top (``floatingCards``; a click goes back to the normal view and
flashes the view, ``flyTo``), and a faint outline marks where each floats over the
window-frame guide, so the wires to it still land.

A collapsed view (#114) is a card the size of its title bar (``collapsed``), with a
collapsed mark and no snapshot; a resize there changes its width only, as in the normal
view.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from specto_core.gestures import delete_link

from .chains import NODE_PAD_PX, PROJECT_NODE, NodePlacer
from .snapshots import DoneFn, SnapshotStore, age_text

if TYPE_CHECKING:
    from .controller import CanvasController

#: ``capture(view_ids, on_done) -> requested view ids``; ``on_done(view_id)`` per landing
CaptureFn = Callable[[list[str], DoneFn], Any]
MIN_SIZE = 120.0  # a card never shrinks below this while resizing
GRAB_WAIT_MS = 150  # the way in waits at most this long for the snapshots to land
HEADER_PX = 30  # a card's header height on screen (OverCard.qml); nodes keep off it


class OverworldController(QObject):
    activeChanged = Signal()
    changed = Signal()
    selectionChanged = Signal()
    #: the link nodes' places changed (a wire, a card, a chip's size or the zoom)
    nodesChanged = Signal()
    #: "Ask agent to change": the text the prompt bar should start from
    askAgentRequested = Signal(str)

    def __init__(
        self,
        controller: CanvasController,
        snapshots: SnapshotStore,
        *,
        capture: CaptureFn | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.snapshots = snapshots
        self.capture = capture
        self.clock = clock
        self._active = False
        self._selected_link = ""
        self._pending: set[str] | None = None  # snapshots still to land on the way in
        self._nodes = NodePlacer(HEADER_PX, NODE_PAD_PX)  # the link nodes' places (#90)
        self._grab_wait = QTimer(self)
        self._grab_wait.setSingleShot(True)
        self._grab_wait.setInterval(GRAB_WAIT_MS)
        self._grab_wait.timeout.connect(self._activate)
        controller.viewsChanged.connect(self.changed)
        controller.wiresChanged.connect(self.changed)
        controller.statusChanged.connect(self.changed)
        self.changed.connect(self._nodes_dirty)

    # -- document access -----------------------------------------------------------------
    @property
    def live(self):
        return self.controller.live

    @property
    def data(self) -> dict[str, Any]:
        return self.controller.data

    # -- show / hide --------------------------------------------------------------------------
    @Property(bool, notify=activeChanged)
    def active(self) -> bool:
        return self._active

    @Slot()
    def show(self) -> None:
        """Request the snapshots; the overworld activates once they land (or after
        ``GRAB_WAIT_MS``)."""
        if self._active or self._pending is not None:
            return
        on_screen = [vid for vid, v in self.controller.views.items() if not v.get("paused")]
        requested: list[str] = []
        if self.capture is not None and on_screen:
            try:
                requested = list(self.capture(on_screen, self._snapshot_landed) or [])
            except Exception as e:  # a grab must never block the overworld
                print(f"[WARN] snapshot on overworld: {type(e).__name__}: {e}")
        self._pending = set(requested)
        if self._pending:
            self._grab_wait.start()
        else:
            self._activate()

    def _snapshot_landed(self, view_id: str) -> None:
        """A snapshot reached the store: the card refreshes; the last one opens the
        overworld (a late one after the wait just refreshes)."""
        if self._pending is not None:
            self._pending.discard(view_id)
            if not self._pending:
                self._activate()
                return
        self.changed.emit()

    @Slot()
    def _activate(self) -> None:
        self._grab_wait.stop()
        self._pending = None
        if self._active:
            return
        self.controller.set_all_paused(True)
        self._active = True
        self.activeChanged.emit()
        self.changed.emit()

    @property
    def opening(self) -> bool:
        """Snapshots requested, the overworld not yet shown."""
        return self._pending is not None

    @Slot()
    def hide(self) -> None:
        if self._pending is not None:  # still on the way in: that way is cancelled
            self._grab_wait.stop()
            self._pending = None
        if not self._active:
            return
        self._active = False
        self.selectLink("")
        self.controller.set_all_paused(False)
        self.activeChanged.emit()
        self.changed.emit()

    @Slot()
    def toggle(self) -> None:
        if self._active or self._pending is not None:
            self.hide()
        else:
            self.show()

    # -- model ----------------------------------------------------------------------------------
    @Property("QVariantList", notify=changed)
    def cards(self) -> list[dict[str, Any]]:
        """One card per view: its document rect, chain colour, health, lock, snapshot
        and the port circles' facts (#81: ``inPorts``, ``inTip``, ``icon``, ``outLinked``,
        ``outTip``, as ``controller.views``)."""
        out = []
        for vid, v in self.controller.views.items():
            x, y, w, h = self.controller.rect_of(vid)
            age = self.snapshots.age_s(vid)
            out.append(
                {
                    "id": vid,
                    "label": v["label"],
                    "type": v["type"],
                    "rect": {"x": x, "y": y, "w": w, "h": h},
                    "floating": bool(v.get("floating")),
                    "collapsed": bool(v.get("collapsed")),
                    "locked": v["locked"],
                    "health": v["health"],
                    "paused": v["paused"],
                    "chain": v.get("chain", ""),
                    "inPorts": v.get("inPorts", []),
                    "inTip": v.get("inTip", ""),
                    "icon": v.get("icon", ""),
                    "outLinked": bool(v.get("outLinked")),
                    "outTip": v.get("outTip", ""),
                    "snapshot": self.snapshots.url(vid),
                    "age": age_text(age),
                    "hasSnapshot": age is not None,
                }
            )
        return out

    @Property("QVariantMap", notify=changed)
    def cardMap(self) -> dict[str, dict[str, Any]]:
        return {c["id"]: c for c in self.cards}

    @Property("QVariantList", notify=changed)
    def floatingCards(self) -> list[dict[str, Any]]:
        """The floating views (#112), in document order: the strip along the top."""
        return [c for c in self.cards if c["floating"]]

    @Slot(str)
    def flyTo(self, view_id: str) -> None:
        """A floating view's chip: back to the normal view, the view flashing."""
        self.hide()
        self.controller.flyTo(view_id)

    @Property("QVariantList", notify=changed)
    def wires(self) -> list[dict[str, Any]]:
        """The controller's curves (``selectedLink`` says which one is selected)."""
        return self.controller.wires

    @Property("QVariantList", notify=changed)
    def wireIds(self) -> list[str]:
        return self.controller.wireIds

    @Property("QVariantMap", notify=changed)
    def wireMap(self) -> dict[str, dict[str, Any]]:
        return self.controller.wireMap

    # -- link nodes: off the card headers (#90) ----------------------------------------------
    @Slot(str, float, float)
    def setNodeSize(self, link_id: str, w: float, h: float) -> None:
        """A link node's chip is ``w`` x ``h`` canvas px (the QML reports it; chips keep
        their screen size, so it changes with the zoom)."""
        if self._nodes.set_size(link_id, w, h):
            self.nodesChanged.emit()

    @Slot(float)
    def setScreenScale(self, ui: float) -> None:
        """Canvas px per screen px: the header band and the clearance scale with it."""
        if self._nodes.set_scale(ui):
            self.nodesChanged.emit()

    @Slot()
    def _nodes_dirty(self) -> None:
        self._nodes.invalidate()
        self.nodesChanged.emit()

    @Property("QVariantMap", notify=nodesChanged)
    def nodePlaces(self) -> dict[str, dict[str, float]]:
        """``link id -> {x, y}``: the centre of each link node in canvas px, on its wire
        and clear of every card header and port circle (``chains.NodePlacer``)."""
        return self.controller.place_nodes(self._nodes)

    @Property("QVariantMap", notify=changed)
    def bounds(self) -> dict[str, float]:
        """The rect holding every card and the project node (what fit shows)."""
        rects = [PROJECT_NODE, *self.controller.rects().values()]
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects)
        y1 = max(r[1] + r[3] for r in rects)
        return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}

    @Property(int, notify=changed)
    def pausedCount(self) -> int:
        return self.controller.pausedCount

    # -- move / resize: free while held, snapped on release -----------------------------------
    @Slot(str, float, float, result="QVariantMap")
    def dragMove(self, view_id: str, x: float, y: float) -> dict[str, float]:
        if view_id not in self.data["views"]:
            return {}
        _, _, w, h = self.controller.rect_of(view_id)
        # the wires and link nodes follow the card while it is held
        self.controller.set_drag_rect(view_id, (float(x), float(y), w, h))
        return {"x": float(x), "y": float(y), "w": w, "h": h}

    @Slot(str)
    def cancelDrag(self, view_id: str) -> None:
        self.controller.cancelDrag(view_id)

    @Slot(str, float, float, result=bool)
    def endMove(self, view_id: str, x: float, y: float) -> bool:
        """The drop resolves snapped into the normal view: one user submit."""
        if view_id not in self.data["views"]:
            return False
        sx, sy, _, _ = self.controller.snapped_move(view_id, x, y)
        return self.controller.endMove(view_id, sx, sy)

    @Slot(str, float, float, float, float, "QVariantList", result="QVariantMap")
    def dragResize(
        self, view_id: str, x: float, y: float, w: float, h: float, edges: list[str]
    ) -> dict[str, float]:
        if view_id not in self.data["views"]:
            return {}
        x0, y0, w0, h0 = self.controller.rect_of(view_id)
        w, h = max(float(w), MIN_SIZE), max(float(h), MIN_SIZE)
        if self.data["views"][view_id]["layout"].get("collapsed"):
            h, y = h0, y0  # a title bar: its width only (#114)
        # a left or top edge that hit the minimum keeps the far edge where it was
        x = x0 + w0 - w if "left" in edges else float(x)
        y = y0 + h0 - h if "top" in edges else float(y)
        self.controller.set_drag_rect(view_id, (x, y, w, h))
        return {"x": x, "y": y, "w": w, "h": h}

    @Slot(str, float, float, float, float, "QVariantList", result=bool)
    def endResize(
        self, view_id: str, x: float, y: float, w: float, h: float, edges: list[str]
    ) -> bool:
        if view_id not in self.data["views"]:
            return False
        free = self.dragResize(view_id, x, y, w, h, edges)
        self.controller.set_drag_rect(view_id, None)
        rect = (free["x"], free["y"], free["w"], free["h"])
        sx, sy, sw, sh = self.controller.snapped_resize(view_id, rect, [str(e) for e in edges])
        return self.controller.endResize(view_id, sx, sy, sw, sh, [str(e) for e in edges])

    # -- link nodes: the link view, delete, ask ---------------------------------------------
    @Property(str, notify=selectionChanged)
    def selectedLink(self) -> str:
        return self._selected_link

    @Slot(str)
    def selectLink(self, link_id: str) -> None:
        if link_id and link_id not in self.data["links"]:
            link_id = ""
        if link_id != self._selected_link:
            self._selected_link = link_id
            self.selectionChanged.emit()

    def _link(self, link_id: str) -> dict[str, Any] | None:
        return self.data["links"].get(link_id)

    @staticmethod
    def _port(ref: dict[str, Any]) -> str:
        return f"{ref['view']}.{ref['port']}"

    @Slot(str, result="QVariantMap")
    def linkSummary(self, link_id: str) -> dict[str, Any]:
        """A link's summary: the sentence, kind, ends and lock (the link view's head)."""
        link = self._link(link_id)
        if link is None:
            return {}
        return {
            "id": link_id,
            "kind": link.get("kind", ""),
            "label": link.get("label", ""),
            "sentence": link.get("summary", {}).get("sentence", ""),
            "source": self._port(link["source"]),
            "target": self._port(link["target"]),
            "twoWay": bool(link.get("two_way")),
            "locked": bool(link.get("locked")),
            "chain": self.controller.chain_of(link["target"]["view"]),
        }

    @Slot(str, result="QVariantMap")
    def linkDetails(self, link_id: str) -> dict[str, Any]:
        """The read-only link view: the summary plus the parameters as JSON, the Python
        reference and code when there is one, and the runtime's status."""
        link = self._link(link_id)
        if link is None:
            return {}
        summary = self.linkSummary(link_id)
        py = link.get("python") or {}
        code = ""
        if py.get("path"):
            # python.path is already relative to the project (".specto/links/<name>.py")
            path = Path(self.controller.engine.project.root) / py["path"]
            try:
                code = path.read_text(encoding="utf-8")
            except OSError as e:
                code = f"[ERROR] cannot read {path}: {e}"
        status = self._status(link_id)
        return {
            **summary,
            "params": json.dumps(link.get("params", {}), indent=2, sort_keys=True),
            "python": f"{py['path']}:{py.get('entry', '')}" if py else "",
            "code": code,
            "status": status.get("state", ""),
            "statusMessage": status.get("message", ""),
        }

    def _status(self, link_id: str) -> dict[str, Any]:
        try:
            return self.controller.engine.links.status(link_id) or {}
        except Exception:
            return {}

    @Slot(str, result=bool)
    def deleteLink(self, link_id: str) -> bool:
        """Delete: one user submit; the selection goes with the link."""
        ok = self.controller.gesture(lambda: delete_link(self.live, link_id), "delete link")
        if ok and self._selected_link == link_id:
            self.selectLink("")
        return ok

    @Slot(str, str)
    def notify(self, level: str, message: str) -> None:
        n = self.controller.notices
        if n is not None:
            getattr(n, level if level in ("info", "warn", "error") else "info")(message)

    @Slot(str, result=str)
    def askAgent(self, link_id: str) -> str:
        """Ask agent to change: the prompt bar starts from the link and its sentence."""
        link = self._link(link_id)
        if link is None:
            return ""
        sentence = link.get("summary", {}).get("sentence", "")
        text = f'Change link {link_id} ("{sentence}") so that '
        self.askAgentRequested.emit(text)
        return text


__all__ = ["GRAB_WAIT_MS", "HEADER_PX", "MIN_SIZE", "OverworldController"]
