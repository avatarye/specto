"""The canvas input model of the normal view (spec #35 story 63, user fix after #40): the
mouse wheel never pans the canvas (a view under it scrolls itself, the empty canvas does
nothing); the pan is a middle drag, or Space held plus a left drag, anywhere over the
area, views included, like Figma; a left drag on the empty canvas is a selection marquee
and views have a selected status (navigation state, carried in the prompt context); the
right button is reserved. Space in a text field types a space. Offscreen Qt, synthesised
wheel/key/mouse events."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item

NOMOD = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
CTRL = Qt.KeyboardModifier.ControlModifier
LEFT = Qt.MouseButton.LeftButton
MIDDLE = Qt.MouseButton.MiddleButton
RIGHT = Qt.MouseButton.RightButton
AGENT = CallContext(connection="mcp:1", origin="agent:s1")


@pytest.fixture
def project(tmp_path):
    """A project with enough files at its root that the file tree (280 x 900, 24 px
    rows) scrolls."""
    from specto_core import Project

    proj = Project.init(tmp_path / "proj", "demo")
    for i in range(60):
        (proj.root / f"note_{i:02d}.md").write_text("x", encoding="utf-8")
    return proj


def scene_point(app, name: str, fx: float = 0.5, fy: float = 0.5) -> QPoint:
    item = find_item(app.window.contentItem(), name)
    assert item is not None, name
    r = item.boundingRect()
    return item.mapToScene(r.topLeft() + QPointF(r.width() * fx, r.height() * fy)).toPoint()


def wheel(app, at: QPoint, dy: int = -120) -> None:
    """One wheel notch at a window point (negative dy: scroll down)."""
    ev = QWheelEvent(
        QPointF(at),
        QPointF(app.window.mapToGlobal(at)),
        QPoint(),
        QPoint(0, dy),
        Qt.MouseButton.NoButton,
        NOMOD,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QCoreApplication.sendEvent(app.window, ev)


def drag(app, spin, p0: QPoint, dx: int, dy: int, button=LEFT, mods=NOMOD, held=None) -> None:
    """Press, move in steps, release; ``held`` runs while the button is still down."""
    QTest.mousePress(app.window, button, mods, p0)
    for i in range(1, 5):
        QTest.mouseMove(app.window, p0 + QPoint(dx * i // 4, dy * i // 4))
        spin(5)
    if held is not None:
        held()
    QTest.mouseRelease(app.window, button, mods, p0 + QPoint(dx, dy))
    spin(10)


def canvas_point(app, cx: float, cy: float) -> QPoint:
    """A canvas point as a window point (pan 0: the area's padded top-left is the origin)."""
    area = find_item(app.window.contentItem(), "canvasArea")
    return area.mapToScene(QPointF(12 + cx, 12 + cy)).toPoint()


def add_view(app, vid: str, label: str, position: list, size: list = (640, 400)) -> None:
    doc = app.live.document()
    doc["views"][vid] = {
        "type": "gallery",
        "label": label,
        "layout": {"position": list(position), "size": list(size)},
    }
    pin(doc, vid)
    app.engine.call("canvas.submit", {"document": doc, "label": f"add {label}"}, AGENT)


def layout(app, vid: str) -> dict:
    return app.live.data["views"][vid]["layout"]


def tree_list(app, spin):
    spin(until=lambda: find_item(app.window.contentItem(), "fileTreeList:fege") is not None)
    lst = find_item(app.window.contentItem(), "fileTreeList:fege")
    spin(until=lambda: lst.property("contentHeight") > lst.height())
    return lst


def test_the_wheel_over_the_file_tree_scrolls_the_tree_and_not_the_canvas(app, spin):
    lst = tree_list(app, spin)
    assert lst.property("contentY") == 0
    at = scene_point(app, "fileTreeList:fege")
    for _ in range(3):
        wheel(app, at)
        spin(30)
    spin(until=lambda: lst.property("contentY") > 0)
    assert app.controller.pan == {"x": 0.0, "y": 0.0}
    # scrolled to the end, the wheel still stays in the tree
    lst.setProperty("contentY", lst.property("contentHeight") - lst.height())
    for _ in range(3):
        wheel(app, at)
        spin(30)
    assert app.controller.pan == {"x": 0.0, "y": 0.0}


def test_the_wheel_over_the_empty_canvas_leaves_the_pan_alone(app, spin):
    tree_list(app, spin)
    area = find_item(app.window.contentItem(), "canvasArea")
    at = area.mapToScene(QPointF(area.width() - 100, area.height() / 2)).toPoint()
    for dy in (-120, 120, -120):
        wheel(app, at, dy)
        spin(20)
    assert app.controller.pan == {"x": 0.0, "y": 0.0}


def test_space_and_a_drag_over_a_view_pans_by_the_delta_and_lands_one_navigate(app, spin):
    tree_list(app, spin)
    navigates: list[tuple] = []
    real = app.live.navigate
    app.live.navigate = lambda origin: navigates.append(tuple(origin)) or real(origin)

    p0 = scene_point(app, "fileTreeList:fege")  # over the file tree, not the empty canvas
    QTest.keyPress(app.window, Qt.Key.Key_Space)
    spin(5)
    assert app.controller.spaceHeld is True
    drag(app, spin, p0, -200, -80)
    QTest.keyRelease(app.window, Qt.Key.Key_Space)
    spin(5)
    assert app.controller.spaceHeld is False

    assert app.controller.pan == {"x": 200.0, "y": 80.0}
    spin(until=lambda: navigates, timeout=2)
    spin(100)
    assert navigates == [(200.0, 80.0)]
    rect = app.live.data["window_frame"]["rect"]
    assert (rect["x"], rect["y"]) == (200.0, 80.0)
    assert app.controller.undoDepth == 0  # navigation, not an edit; the tree took no drag

    # without Space the same drag over the tree pans nothing
    drag(app, spin, p0, -100, 0)
    assert app.controller.pan == {"x": 200.0, "y": 80.0}


def test_a_middle_drag_over_a_view_pans_by_the_delta_and_lands_one_navigate(app, spin):
    tree_list(app, spin)
    navigates: list[tuple] = []
    real = app.live.navigate
    app.live.navigate = lambda origin: navigates.append(tuple(origin)) or real(origin)

    drag(app, spin, scene_point(app, "fileTreeList:fege"), -150, 60, button=MIDDLE)
    assert app.controller.pan == {"x": 150.0, "y": -60.0}
    spin(until=lambda: navigates, timeout=2)
    spin(100)
    assert navigates == [(150.0, -60.0)]
    assert app.controller.undoDepth == 0 and app.controller.selectedViews == []
    # the hand is no longer up: a plain left drag over the tree pans nothing
    drag(app, spin, scene_point(app, "fileTreeList:fege"), -100, 0)
    assert app.controller.pan == {"x": 150.0, "y": -60.0}


def assert_not_visible(item) -> None:
    assert item is not None and item.isVisible() is False


def test_the_right_button_starts_no_drag_no_pan_and_no_marquee(app, spin):
    """Reserved for a context menu: a right drag on the empty canvas, on a header and in
    a view changes nothing."""
    add_view(app, "kobi", "Front", [300, 40])
    spin(until=lambda: "kobi" in app.hosts.hosts)
    app.controller.selectView("kobi", False)
    marquee = find_item(app.window.contentItem(), "marquee")
    for name, fx in (("canvasArea", 0.8), ("header:kobi", 0.3), ("slot:kobi", 0.5)):
        drag(
            app,
            spin,
            scene_point(app, name, fx, 0.5),
            -120,
            -40,
            button=RIGHT,
            held=lambda: spin(5) or assert_not_visible(marquee),
        )
    assert app.controller.pan == {"x": 0.0, "y": 0.0}
    assert layout(app, "kobi") == {"position": [300.0, 40.0], "size": [640.0, 400.0]}
    assert app.controller.selectedViews == ["kobi"]
    assert app.controller.undoDepth == 1


def test_a_left_drag_on_the_empty_canvas_is_a_marquee_that_selects_the_views_it_touches(app, spin):
    add_view(app, "kobi", "Front", [300, 40])
    spin(until=lambda: "kobi" in app.hosts.hosts)
    marquee = find_item(app.window.contentItem(), "marquee")
    assert marquee is not None and not marquee.isVisible()
    # the prompt bar may be sliding out of the way after a stale hover at window birth;
    # a press that lands on its input while it passes is no marquee
    panel = find_item(app.window.contentItem(), "promptPanel")
    spin(until=lambda: not panel.isVisible())
    seen: list[bool] = []

    def while_held():
        spin(5)
        seen.append(marquee.isVisible())
        # accent at low alpha, so what it covers stays readable
        assert marquee.property("color").alphaF() < 0.3

    # from the empty canvas at (1000, 600) up-left to (800, 300): over the front gallery
    # (300..940 x 40..440), clear of the file tree (0..280)
    drag(app, spin, canvas_point(app, 1000, 600), -200, -300, held=while_held)
    assert seen == [True] and not marquee.isVisible()
    assert app.controller.selectedViews == ["kobi"]
    assert app.controller.views["kobi"]["selected"] is True
    assert app.controller.views["fege"]["selected"] is False
    assert app.controller.pan == {"x": 0.0, "y": 0.0}  # a marquee never pans

    # Shift adds: a marquee below the tree's bottom edge (900) reaching up into it
    drag(app, spin, canvas_point(app, 290, 906), -90, -40, mods=SHIFT)
    assert app.controller.selectedViews == ["kobi", "fege"]
    assert app.hosts.ctx.selected_views() == [
        "fege",
        "kobi",
    ]  # what open-file reuses, newest first
    # a plain marquee that touches nothing clears
    drag(app, spin, canvas_point(app, 1100, 700), 60, 60)
    assert app.controller.selectedViews == []
    assert app.controller.undoDepth == 1  # selection is navigation state


def test_header_clicks_select_and_toggle_and_the_empty_canvas_or_esc_clears(app, spin):
    add_view(app, "kobi", "Front", [300, 40])
    add_view(app, "tamu", "Rear", [300, 460])
    spin(until=lambda: "tamu" in app.hosts.hosts)

    QTest.mouseClick(app.window, LEFT, NOMOD, scene_point(app, "header:kobi", 0.3, 0.5))
    spin(10)
    assert app.controller.selectedViews == ["kobi"] and app.controller.focusedView == "kobi"
    QTest.mouseClick(app.window, LEFT, CTRL, scene_point(app, "header:tamu", 0.3, 0.5))
    spin(10)
    assert app.controller.selectedViews == ["kobi", "tamu"]
    QTest.mouseClick(app.window, LEFT, SHIFT, scene_point(app, "header:kobi", 0.3, 0.5))
    spin(10)
    assert app.controller.selectedViews == ["tamu"]
    QTest.mouseClick(app.window, LEFT, CTRL, scene_point(app, "header:kobi", 0.3, 0.5))
    spin(10)
    assert app.controller.selectedViews == ["tamu", "kobi"]
    # a plain click on one header of a multi-selection collapses to it
    QTest.mouseClick(app.window, LEFT, NOMOD, scene_point(app, "header:tamu", 0.3, 0.5))
    spin(10)
    assert app.controller.selectedViews == ["tamu"]
    # a header drag still moves the view (and keeps it selected)
    drag(app, spin, scene_point(app, "header:tamu", 0.3, 0.5), 60, 0)
    assert layout(app, "tamu")["position"] == [360.0, 460.0]
    assert app.controller.selectedViews == ["tamu"]

    QTest.mouseClick(app.window, LEFT, NOMOD, scene_point(app, "canvasArea", 0.8, 0.5))
    spin(10)
    assert app.controller.selectedViews == []
    app.controller.selectView("kobi", False)
    app.controller.clearSelection()  # what Esc does
    assert app.controller.selectedViews == []
    # a selected view that goes leaves the selection
    app.controller.selectView("kobi", False)
    app.controller.removeView("kobi")
    spin(10)
    assert app.controller.selectedViews == []


def test_the_selected_views_travel_in_the_prompt_context(app, spin):
    from specto_app.prompt.context import render_context

    add_view(app, "kobi", "Front", [300, 40])
    spin(until=lambda: "kobi" in app.hosts.hosts)
    assert app.prompt_bar.context().selected_views == []
    app.controller.selectView("kobi", False)
    ctx = app.prompt_bar.context()
    assert ctx.selected_views == ["kobi"]
    assert '"selected_views":["kobi"]' in render_context(ctx)


def test_space_in_the_prompt_bar_types_a_space_and_does_not_arm_the_pan(app, spin):
    tree_list(app, spin)
    prompt = find_item(app.window.contentItem(), "promptBar")
    prompt.focusInput()
    spin(until=lambda: find_item(app.window.contentItem(), "promptInput").hasActiveFocus())
    field = find_item(app.window.contentItem(), "promptInput")

    QTest.keyClick(app.window, Qt.Key.Key_Space)
    spin(5)
    assert field.property("text") == " "
    QTest.keyPress(app.window, Qt.Key.Key_Space)
    spin(5)
    assert app.controller.spaceHeld is False
    drag(app, spin, scene_point(app, "canvasArea", 0.8, 0.5), -100, 0)
    QTest.keyRelease(app.window, Qt.Key.Key_Space)
    assert app.controller.pan == {"x": 0.0, "y": 0.0}
