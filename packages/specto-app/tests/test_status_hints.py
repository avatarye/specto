"""#80: the view header shows ``<name>: <label>``, the health dot and the buttons; every
hint (the empty-state line, health messages, gesture hints) lives in the status bar,
spoken for the hovered view, else the selected one."""

from __future__ import annotations

import test_shell
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from test_shell import gallery_doc, needs_testdata, submit

from specto_app.app import find_item
from specto_app.canvas.controller import AUTO_UPDATE_OFF, GESTURES

pytestmark = needs_testdata
# the shell offscreen over a frames project, as test_shell runs it
frames_project = test_shell.frames_project
app = test_shell.app

PICK = "Pick files in the file tree"
#: the gallery fed by an intaker also says it does not follow its files by itself (#102)
KOBI = f"kobi: nothing picked  ·  {PICK}  ·  {AUTO_UPDATE_OFF}"


def _text(app, name: str) -> str:
    item = find_item(app.window.contentItem(), name)
    assert item is not None, name
    return item.property("text")


def test_status_bar_speaks_for_the_hovered_view_else_the_selected_one(app, spin):
    submit(app, gallery_doc(app), "gallery")
    spin(until=lambda: app.controller.views.get("kobi", {}).get("health") == "paused")
    assert app.controller.statusHint == ""
    app.controller.hoverView("kobi")
    assert app.controller.hoveredView == "kobi"
    assert app.controller.statusHint == KOBI
    assert _text(app, "statusHint") == app.controller.statusHint
    app.controller.leaveView("kobi")
    assert app.controller.hoveredView == "" and app.controller.statusHint == ""
    # selected: the same words without the gesture line
    app.controller.selectView("kobi", False)
    assert app.controller.statusHint == KOBI
    # hovering another view wins over the selection; a healthy view offers its gestures
    app.controller.hoverView("fege")
    line = app.controller.statusHint
    assert line.startswith("fege: ") and line.endswith(GESTURES)
    app.controller.leaveView("fege")
    assert app.controller.statusHint == KOBI
    # the overworld pauses every host: that is no news about the selected view
    app.controller.hoverView("kobi")
    app.controller.set_all_paused(True)
    assert app.controller.statusHint == "" and app.controller.hoveredView == ""
    app.controller.set_all_paused(False)
    # back: the selected view speaks again, empty-state hint included (#91)
    assert app.controller.statusHint == KOBI
    assert app.controller.views["kobi"]["hint"] == PICK
    app.controller.clearSelection()
    assert app.controller.statusHint == ""


def test_header_is_id_label_dot_and_buttons_and_the_content_hint_is_gone(app, spin):
    submit(app, gallery_doc(app), "gallery")
    spin(until=lambda: "kobi" in app.hosts.hosts)
    assert _text(app, "title:kobi") == "kobi: Cam A"
    assert _text(app, "title:fege") == "fege: Files"
    assert find_item(app.window.contentItem(), "hint:kobi") is None
    assert find_item(app.window.contentItem(), "health:kobi") is not None
    for name in ("lock:kobi", "maximize:kobi", "close:kobi"):
        assert find_item(app.window.contentItem(), name) is not None


def test_the_pointer_over_a_view_hovers_it(app, spin):
    submit(app, gallery_doc(app), "gallery")
    spin(until=lambda: "kobi" in app.hosts.hosts)
    # over the gallery's content, not its header: the native gallery node accepts hover
    # events, so no slot-level hover handler would see this pointer
    slot = find_item(app.window.contentItem(), "slot:kobi")
    p = slot.mapToScene(slot.boundingRect().center()).toPoint()
    QTest.mouseMove(app.window, p)
    spin(until=lambda: app.controller.hoveredView == "kobi")
    assert app.controller.statusHint.startswith("kobi: ")
    header = find_item(app.window.contentItem(), "header:fege")
    QTest.mouseMove(app.window, header.mapToScene(header.boundingRect().center()).toPoint())
    spin(until=lambda: app.controller.hoveredView == "fege")
    area = find_item(app.window.contentItem(), "canvasArea")
    far = area.mapToScene(area.boundingRect().bottomRight()).toPoint() - QPoint(30, 30)
    QTest.mouseMove(app.window, far)
    spin(until=lambda: app.controller.hoveredView == "")
