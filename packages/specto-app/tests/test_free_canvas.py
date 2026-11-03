"""The free canvas in the normal view (#40, spec #35 stories 37, 63-66): the canvas
pans at 1:1, views outside the viewport pause and resume when panned back, Home brings
the file tree back, move and resize are user submits with snapping (one undo step each),
and the window frame follows the app window. Offscreen Qt; real mouse input is
the scripted flow tools/gui_flows/v2-40-free-canvas.txt."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
NOMOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton


def drag(
    app, spin, name: str, dx: int, dy: int, fx: float = 0.5, fy: float = 0.5, held=None
) -> None:
    """Press on an item, move in steps, release: what a real mouse does. ``held`` runs
    once the pointer is at its last position, before the release."""
    item = find_item(app.window.contentItem(), name)
    assert item is not None, name
    r = item.boundingRect()
    p0 = item.mapToScene(r.topLeft() + QPoint(int(r.width() * fx), int(r.height() * fy))).toPoint()
    QTest.mousePress(app.window, LEFT, NOMOD, p0)
    for i in range(1, 5):
        QTest.mouseMove(app.window, p0 + QPoint(dx * i // 4, dy * i // 4))
        spin(5)
    if held is not None:
        held()
    QTest.mouseRelease(app.window, LEFT, NOMOD, p0 + QPoint(dx, dy))
    spin(10)


def submit(app, doc: dict, label: str) -> dict:
    return app.engine.call("canvas.submit", {"document": doc, "label": label}, AGENT)


def layout(app, vid: str = "kobi") -> dict:
    return app.live.data["views"][vid]["layout"]


def add_view(app, vid: str, label: str, position: list, size: list = (640, 400)) -> dict:
    doc = app.live.document()
    doc["views"][vid] = {
        "type": "gallery",
        "label": label,
        "layout": {"position": list(position), "size": list(size)},
    }
    pin(doc, vid)
    return submit(app, doc, f"add {label}")


def test_views_outside_the_viewport_pause_and_resume_when_panned_back(app, spin):
    add_view(app, "kobi", "Far", [3000, 0])
    spin(until=lambda: "kobi" in app.hosts.hosts)
    spin(until=lambda: app.controller.views["kobi"]["paused"])
    assert app.controller.views["fege"]["paused"] is False
    assert app.engine.call("shell.locate", {"name": "slot:kobi"})["visible"] is False
    assert app.controller.pausedCount == 1

    app.controller.panBy(2800, 0)
    spin(until=lambda: app.controller.views["fege"]["paused"])
    assert app.controller.views["kobi"]["paused"] is False
    assert app.controller.pan == {"x": 2800.0, "y": 0.0}
    assert app.engine.call("shell.locate", {"name": "slot:kobi"})["visible"] is True
    vp = app.controller.viewport
    assert vp["x"] == 2800.0 - 12 and vp["w"] > 1000  # canvas px: the area's left edge

    # the Home key (an application shortcut) is exercised by the scripted flow; offscreen,
    # shortcuts need an active window, which a second window in one process never gets
    app.controller.home()
    spin(until=lambda: not app.controller.views["fege"]["paused"])
    assert app.controller.pan == {"x": 0.0, "y": 0.0}
    assert app.controller.views["kobi"]["paused"] is True
    # panning is navigation, not a document edit
    assert app.controller.undoDepth == 1


def test_dragging_a_header_moves_the_view_snapped_as_one_user_submit(app, spin):
    add_view(app, "kobi", "Front", [380, 40])
    spin(until=lambda: "kobi" in app.hosts.hosts)

    # 14 px short of the file tree's right edge and 7 px below the frame's top: both snap
    drag(app, spin, "header:kobi", -14, -33, fx=0.3)

    assert layout(app) == {"position": [360.0, 0.0], "size": [640.0, 400.0]}
    rec = app.live.history[-1]
    assert (rec.origin, rec.label) == ("user", "move Front") and app.controller.undoDepth == 2
    assert next(e for e in app.controller.viewIds if e["id"] == "kobi")["x"] == 360.0
    view = find_item(app.window.contentItem(), "view:kobi")
    assert view.mapToScene(view.boundingRect().topLeft()).x() == 12 + 360  # pad + canvas x
    slot = app.hosts.slot_of("kobi")
    assert slot.mapToScene(slot.boundingRect().topLeft()).x() == 12 + 360 + 3  # gutter + clip inset

    # far from every border: the view lands where it was dropped
    drag(app, spin, "header:kobi", 60, 80, fx=0.3)
    assert app.live.data["views"]["kobi"]["layout"]["position"] == [420.0, 80.0]
    assert app.controller.undoDepth == 3


def card_x(app, vid: str) -> float:
    card = find_item(app.window.contentItem(), f"view:{vid}")
    return card.mapToScene(card.boundingRect().topLeft()).x()


def test_dragging_one_view_of_a_multi_selection_moves_the_group_as_one_submit(app, spin):
    """#84: every selected view moves by the dragged view's snapped delta, locked ones
    included, in one undo step; an unselected view dragged moves alone."""
    add_view(app, "kobi", "Front", [380, 40])
    add_view(app, "tamu", "Rear", [380, 460])
    add_view(app, "rena", "Side", [1000, 40])
    spin(until=lambda: "rena" in app.hosts.hosts)
    assert app.controller.toggleLock("tamu")
    app.controller.setSelection(["kobi", "tamu"])
    depth = app.controller.undoDepth

    def while_held():
        # the whole group follows the pointer, not only the dragged card
        assert card_x(app, "kobi") < 12 + 380 and card_x(app, "tamu") < 12 + 380
        assert card_x(app, "rena") == 12 + 1000

    # kobi snaps to the tree's edge and the frame's top (delta -20, -40); tamu follows
    drag(app, spin, "header:kobi", -14, -33, fx=0.3, held=while_held)
    assert layout(app, "kobi")["position"] == [360.0, 0.0]
    assert layout(app, "tamu")["position"] == [360.0, 420.0]
    assert layout(app, "rena")["position"] == [1000.0, 40.0]
    assert app.controller.undoDepth == depth + 1
    rec = app.live.history[-1]
    assert (rec.origin, rec.label) == ("user", "move 2 views")
    assert app.live.data["views"]["tamu"]["locked"] is True
    assert app.controller.selectedViews == ["kobi", "tamu"]
    assert card_x(app, "tamu") == 12 + 360

    # one undo restores both
    assert app.controller.undo()
    assert layout(app, "kobi")["position"] == [380.0, 40.0]
    assert layout(app, "tamu")["position"] == [380.0, 460.0]

    # dragging an unselected view moves only that view (and selects it)
    drag(app, spin, "header:rena", 60, 80, fx=0.3)
    assert layout(app, "rena")["position"] == [1060.0, 120.0]
    assert layout(app, "kobi")["position"] == [380.0, 40.0]
    assert app.controller.selectedViews == ["rena"]
    assert app.live.history[-1].label == "move Side"


def test_dragging_a_corner_resizes_the_view_snapped_as_one_user_submit(app, spin):
    add_view(app, "kobi", "Front", [360, 0])
    spin(until=lambda: "kobi" in app.hosts.hosts)

    # the bottom-right corner to 8 px short of the frame's right edge and 7 px above its
    # bottom (the window frame is 1600x900): both borders snap to the frame
    drag(app, spin, "resize:kobi:bottom-right", 1600 - 8 - 1000, 900 - 7 - 400)
    assert layout(app) == {"position": [360.0, 0.0], "size": [1240.0, 900.0]}
    rec = app.live.history[-1]
    assert (rec.origin, rec.label) == ("user", "resize Front") and app.controller.undoDepth == 2

    # the left border, 6 px past the file tree's right edge: it snaps back and the origin
    # moves with it, one step
    drag(app, spin, "resize:kobi:left", 6, 0)
    assert layout(app) == {"position": [360.0, 0.0], "size": [1240.0, 900.0]}
    assert app.controller.undoDepth == 2  # snapped back to where it was: nothing to submit
    drag(app, spin, "resize:kobi:left", 40, 0)
    assert layout(app) == {"position": [400.0, 0.0], "size": [1200.0, 900.0]}
    assert app.controller.undoDepth == 3

    assert app.controller.undo() and app.controller.undo()
    assert layout(app) == {"position": [360.0, 0.0], "size": [640.0, 400.0]}


def test_the_window_frame_follows_the_app_window_with_no_guide_drawn(app, spin):
    """The window frame is the canvas area inside its padding: the window opens so the
    frame fits exactly, and a window resize writes the frame back as one user step. No
    guide is drawn for it (#120); the quotas still count what it holds."""
    area = find_item(app.window.contentItem(), "canvasArea")
    assert (area.width() - 24, area.height() - 24) == (1600, 900)
    assert find_item(app.window.contentItem(), "windowFrameGuide") is None

    app.window.resize(1300, 700)
    spin(until=lambda: app.live.data["window_frame"]["rect"]["w"] != 1600, timeout=3)
    rect = app.live.data["window_frame"]["rect"]
    assert (rect["w"], rect["h"]) == (area.width() - 24, area.height() - 24)
    assert rect["w"] < 1300 and rect["h"] < 700 and (rect["x"], rect["y"]) == (0, 0)
    rec = app.live.history[-1]
    assert (rec.origin, rec.label) == ("user", "resize window") and app.controller.undoDepth == 1
    assert app.controller.windowFrame["w"] == rect["w"]
    # the next move snaps to the new frame edge
    add_view(app, "kobi", "Front", [300, 40])
    spin(until=lambda: "kobi" in app.hosts.hosts)
    drag(app, spin, "header:kobi", int(rect["w"] - 640 - 300 + 6), 0, fx=0.3)
    assert app.live.data["views"]["kobi"]["layout"]["position"] == [rect["w"] - 640, 40.0]


def test_a_link_that_resolves_while_its_view_is_off_screen_shows_ok_after_resume(app, spin):
    add_view(app, "kobi", "Far", [3000, 0])
    spin(until=lambda: "kobi" in app.hosts.hosts and app.controller.views["kobi"]["paused"])
    events = app.live.events
    events.emit("link.resolving", {"link": "l2", "target": {"view": "kobi", "port": "data"}})
    spin(10)
    assert app.controller.views["kobi"]["health"] == "paused"  # off screen stays off screen
    events.emit("link.resolved", {"link": "l2", "target": {"view": "kobi", "port": "data"}})
    spin(10)
    app.controller.panBy(2800, 0)
    spin(until=lambda: not app.controller.views["kobi"]["paused"])
    assert app.controller.views["kobi"]["health"] == "ok"


def test_maximise_fills_the_window_with_one_view_and_esc_returns(app, spin):
    """Stories 72 and 32: maximise fills the canvas area with one view, the others pause;
    Esc (``controller.maximize("")``) restores the layout untouched."""
    add_view(app, "kobi", "Front", [300, 40])
    spin(until=lambda: "kobi" in app.hosts.hosts)
    before = app.live.document()

    app.controller.maximize("kobi")
    spin(until=lambda: app.controller.views["fege"]["paused"])
    assert app.controller.maximized == "kobi" and app.controller.views["kobi"]["paused"] is False
    area = find_item(app.window.contentItem(), "canvasArea")
    slot = find_item(app.window.contentItem(), "view:kobi")
    spin(until=lambda: slot.width() == area.width() - 24)
    # it starts under the floating undo/redo/menu cluster (#120)
    top = area.property("maximizedTop")
    assert top > 12 and (slot.x(), slot.y(), slot.height()) == (12, top, area.height() - top - 12)
    assert app.engine.call("shell.locate", {"name": "slot:fege"})["visible"] is False

    app.controller.maximize("")  # what Esc does
    spin(until=lambda: not app.controller.views["fege"]["paused"])
    assert app.controller.maximized == ""
    assert app.live.document() == before and app.controller.undoDepth == 1


def test_panning_moves_the_window_frame_as_navigation_so_quotas_count_what_renders(app, spin):
    """Stories 64 and 75 agree: after a pan the window frame is where the window is; no
    undo step is spent on it."""
    app.controller.panBy(2800, 100)
    app.controller.flush_navigation()
    rect = app.live.data["window_frame"]["rect"]
    assert (rect["x"], rect["y"]) == (2800.0, 100.0)
    assert app.controller.undoDepth == 0
    vp = app.controller.viewport
    assert (vp["x"] + 12, vp["y"] + 12) == (rect["x"], rect["y"])
    assert (vp["w"] - 24, vp["h"] - 24) == (rect["w"], rect["h"])
    app.controller.home()
    app.controller.flush_navigation()
    assert app.live.data["window_frame"]["rect"]["x"] == 0.0


def test_the_header_lock_is_a_user_gesture_the_agent_must_respect(app, spin):
    add_view(app, "kobi", "Front", [300, 40])
    spin(until=lambda: "kobi" in app.hosts.hosts)
    assert app.controller.toggleLock("kobi") is True
    assert app.live.data["views"]["kobi"]["locked"] is True
    assert app.live.history[-1].label == "lock Front" and app.controller.views["kobi"]["locked"]
    doc = app.live.document()
    doc["views"]["kobi"]["layout"]["position"] = [900, 40]
    from specto_core.canvas_document import CanvasInvalid

    with pytest.raises(CanvasInvalid):
        submit(app, doc, "agent move")
    assert app.controller.toggleLock("kobi") is True
    assert app.live.data["views"]["kobi"]["locked"] is False
    assert app.live.history[-1].label == "unlock Front"


def qml_warnings(app) -> list[str]:
    """Every QML warning the engine reports from now on (a TypeError in a handler is one)."""
    out: list[str] = []
    app.qml.warnings.connect(lambda errs: out.extend(e.toString() for e in errs))
    return out


def test_a_gesture_end_keeps_its_card_and_raises_no_qml_error(app, spin):
    """A move re-lays the cards out; the card under the pointer must be the same item
    afterwards (its release handler is still running), not a fresh delegate."""
    add_view(app, "kobi", "Front", [300, 40])
    spin(until=lambda: "kobi" in app.hosts.hosts)
    warnings = qml_warnings(app)
    slot_before = app.hosts.slot_of("kobi")
    card_before = find_item(app.window.contentItem(), "view:kobi")

    drag(app, spin, "header:kobi", 60, 80, fx=0.3)
    assert layout(app)["position"] == [360.0, 120.0]
    assert app.hosts.slot_of("kobi") is slot_before
    card = find_item(app.window.contentItem(), "view:kobi")
    assert card is card_before and card.property("dragging") is False
    drag(app, spin, "resize:kobi:bottom-right", 40, 40)
    assert layout(app)["size"] == [680.0, 440.0]
    assert find_item(app.window.contentItem(), "view:kobi") is card_before
    assert warnings == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing locks")
def test_a_move_lands_in_memory_and_warns_once_while_canvas_json_is_held_open(
    app, spin, monkeypatch
):
    from specto_core import fsio

    monkeypatch.setattr(fsio, "FIRST_DELAY_S", 0.002)
    monkeypatch.setattr(fsio, "MAX_DELAY_S", 0.004)
    add_view(app, "kobi", "Front", [300, 40])
    spin(until=lambda: "kobi" in app.hosts.hosts)
    warnings = qml_warnings(app)
    with open(app.project.canvas_file, encoding="utf-8"):
        drag(app, spin, "header:kobi", 60, 80, fx=0.3)
        assert layout(app)["position"] == [360.0, 120.0]  # the gesture is not lost
        assert find_item(app.window.contentItem(), "view:kobi").property("dragging") is False
        drag(app, spin, "header:kobi", 60, 0, fx=0.3)
        assert layout(app)["position"] == [420.0, 120.0]
        warned = [n for n in app.notices.log if n["level"] == "warn"]
        assert len(warned) == 1 and "canvas.json" in warned[0]["message"] and warned[0]["fix"]
        assert app.project.canvas_document().views["kobi"].layout.position != (420.0, 120.0)
    drag(app, spin, "header:kobi", 0, 60, fx=0.3)
    assert app.live.dirty is False
    assert list(app.project.canvas_document().views["kobi"].layout.position) == [420.0, 180.0]
    assert [n for n in app.notices.log if n["level"] == "warn"] == warned
    assert warnings == []


def test_the_hosts_reveal_brings_a_far_view_on_screen_selected_and_focused(app, spin):
    """#43: a file whose dedicated view is open already is revealed, not duplicated: the
    host context's ``reveal_view`` selects the view, pans it into the viewport and
    focuses it."""
    add_view(app, "kobi", "Far", [3000, 2600])
    spin(until=lambda: "kobi" in app.hosts.hosts)
    assert app.controller.pan == {"x": 0.0, "y": 0.0}
    app.hosts.ctx.reveal_view("kobi")
    spin(50)
    assert app.controller.selectedViews == ["kobi"]
    assert app.controller.focusedView == "kobi"
    x, y, w, h = app.controller.rect_of("kobi")
    vx, vy, vw, vh = app.controller.viewport_rect()
    assert vx <= x and vy <= y and x + w <= vx + vw and y + h <= vy + vh
