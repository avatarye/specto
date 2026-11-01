"""The shell chrome (#120, spec #119 points 1-4): no top bar, undo, redo and the one
global menu float over the canvas's top-right corner; the bottom bar has its own surface
and carries the resolving count; no window-frame guide is drawn (quotas still count the
window); a selected view's highlight is an ink outline with a halo, a colour no link
chain uses. Offscreen Qt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QMetaObject, QObject, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item
from specto_app.theme import COLORS

AGENT = CallContext(connection="mcp:1", origin="agent:s1")


def window_rect(item) -> tuple[float, float, float, float]:
    p = item.mapToScene(item.boundingRect().topLeft())
    return (p.x(), p.y(), item.width(), item.height())


def test_no_top_bar_undo_redo_and_menu_float_over_the_canvas_top_right(app, spin):
    root = app.window.contentItem()
    assert find_item(root, "topBar") is None and find_item(root, "projectButton") is None
    area = find_item(root, "canvasArea")
    ax, ay, aw, _ah = window_rect(area)
    assert ay == 0  # the canvas reaches the window's top edge
    buttons = [find_item(root, n) for n in ("undoButton", "redoButton", "moreButton")]
    assert all(b is not None and b.isVisible() for b in buttons)
    rects = [window_rect(b) for b in buttons]
    # left to right: undo, redo, the menu, near the area's top-right corner
    assert rects[0][0] < rects[1][0] < rects[2][0]
    mx, my, mw, _mh = rects[2]
    assert ax + aw - 40 <= mx + mw <= ax + aw and ay <= my <= ay + 24
    # one pill (design #123 section 1): 22 px buttons, a divider before the menu button
    pill = find_item(root, "canvasControls")
    assert pill.property("radius") == pill.height() / 2 and pill.height() == 32
    assert all(b.width() == b.height() == 22 for b in buttons)
    divider = find_item(root, "controlsDivider")
    assert divider is not None and rects[1][0] < window_rect(divider)[0] < rects[2][0]


def click(app, name: str) -> None:
    item = find_item(app.window.contentItem(), name)
    assert item is not None, name
    p = item.mapToScene(item.boundingRect().center()).toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)


def locate(app, name: str) -> dict:
    return app.engine.call("shell.locate", {"name": name})


def find_all(app, name: str) -> list:
    out, stack = [], [app.window.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            out.append(item)
        stack.extend(item.childItems())
    return out


def test_the_one_global_menu_holds_the_project_entries_and_quit(app, spin):
    click(app, "moreButton")
    # the rows show before the column lays them out (its next polish; a frame later on
    # macOS): until then they all sit on the first row and a click hits the last one
    spin(
        until=lambda: (
            locate(app, "menu:quit")["visible"]
            and locate(app, "menu:quit")["rect"][1] > locate(app, "menu:openFolder")["rect"][1]
        )
    )
    names = ["menu:openFolder", "menu:recent", "menu:quit"]
    # #218: no snap or details rows; details sits behind the status bar's info button
    assert not locate(app, "menu:snapCanvas")["found"]
    assert not locate(app, "menu:details")["found"]
    # #217: no new project row; Open folder... makes a project of a plain folder
    assert not locate(app, "menu:newProject")["found"]
    rows = [locate(app, n) for n in names]
    assert all(r["found"] and r["visible"] for r in rows), rows
    # top to bottom in that order, the whole menu inside the window under the button
    ys = [r["rect"][1] for r in rows]
    assert ys == sorted(set(ys)), ys
    more = locate(app, "moreButton")["rect"]
    assert ys[0] > more[1] + more[3]
    assert all(r["rect"][0] + r["rect"][2] <= app.window.width() for r in rows)

    assert locate(app, "menuDetail:quit")["text"] == "Ctrl+Q"
    # recent projects is a submenu to the menu's left; the open project is listed, bold
    click(app, "menu:recent")
    here = f"menu:recent:{app.project.name}"
    spin(until=lambda: locate(app, here)["visible"])
    assert locate(app, "menu:quit")["visible"]  # the menu stays open
    # (its place follows its width, known once the rows are laid out)
    spin(until=lambda: sum(locate(app, here)["rect"][0::2]) <= rows[2]["rect"][0])
    # other tests' projects share the name: exactly one of them is the open one
    labels = find_all(app, "menuText:" + here.removeprefix("menu:"))
    assert [lb.property("font").bold() for lb in labels].count(True) == 1
    spin(until=lambda: all(lb.width() > 0 for lb in labels))  # the menu fits its rows

    # Details opens from the status bar's info button, above the bar, inside the window
    for menu in ("recentMenu", "moreMenu"):  # the shell's Escape shortcut owns the key
        QMetaObject.invokeMethod(app.window.findChild(QObject, menu), "close")
    spin(until=lambda: not locate(app, "menu:quit")["visible"])
    click(app, "detailsButton")
    spin(until=lambda: locate(app, "statusEndpoint")["visible"])
    endpoint = locate(app, "statusEndpoint")["rect"]
    button = locate(app, "detailsButton")["rect"]
    assert 0 < endpoint[1] and endpoint[1] + endpoint[3] < button[1]


def add_gallery(app, spin, vid: str = "kobi", position=(380, 40)) -> None:
    doc = app.live.document()
    doc["views"][vid] = {
        "type": "gallery",
        "label": vid,
        "layout": {"position": list(position), "size": [480, 300]},
    }
    pin(doc, vid)
    app.engine.call("canvas.submit", {"document": doc, "label": f"add {vid}"}, AGENT)
    spin(until=lambda: vid in app.hosts.hosts)


def inside(item, ancestor_name: str) -> bool:
    while item is not None:
        if item.objectName() == ancestor_name:
            return True
        item = item.parentItem()
    return False


def test_the_bottom_bar_is_its_own_surface_and_counts_what_is_resolving(app, spin):
    root = app.window.contentItem()
    bar = find_item(root, "statusBar")
    _x, by, _w, bh = window_rect(bar)
    assert by + bh == app.window.height()  # compact, at the bottom
    # its own surface tone, a divider along its top edge, apart from the canvas
    surface = find_item(root, "statusSurface")
    divider = find_item(root, "statusDivider")
    assert surface is not None and divider is not None
    assert surface.property("color").name() == QColor(COLORS["bg_tint"]).name()
    assert divider.property("color").name() == QColor(COLORS["border_strong"]).name()
    assert window_rect(divider)[1] == by and divider.height() == 1 and bh == 26

    count = find_item(root, "resolvingCount")
    assert count is not None and inside(count, "statusBar") and not count.isVisible()
    add_gallery(app, spin)
    app.live.events.emit(
        "link.resolving", {"link": "l2", "target": {"view": "kobi", "port": "data"}}
    )
    spin(until=lambda: count.isVisible())
    assert locate(app, "resolvingCount")["text"] == "1 resolving"
    app.live.events.emit(
        "link.resolved", {"link": "l2", "target": {"view": "kobi", "port": "data"}}
    )
    spin(until=lambda: not count.isVisible())


def test_no_window_frame_guide_in_either_level_and_the_controls_float_over_both(app, spin):
    root = app.window.contentItem()
    assert find_item(root, "windowFrameGuide") is None
    ow = app.overworld
    ow.toggle()
    spin(until=lambda: ow.active)
    assert find_item(root, "overWindowFrame") is None
    # fit frames the cards and the project node, not the window's empty rest
    tree = app.controller.rect_of("fege")
    assert ow.bounds == {"x": 0.0, "y": 0.0, "w": tree[0] + tree[2], "h": tree[1] + tree[3]}
    assert locate(app, "undoButton")["visible"] and locate(app, "moreButton")["visible"]
    # the document still keeps the window frame: the quotas count what it holds
    assert app.live.data["window_frame"]["rect"]["w"] == 1600
    ow.toggle()
    spin(until=lambda: not ow.active)


def test_a_selected_view_is_an_ink_outline_with_a_halo_no_chain_colour_uses(app, spin):
    add_gallery(app, spin)
    root = app.window.contentItem()
    chains = {QColor(v).name() for k, v in COLORS.items() if k.startswith("chain_")}
    frame, other = find_item(root, "frame:kobi"), find_item(root, "frame:fege")
    halo = find_item(root, "halo:kobi")
    assert halo is not None and not halo.isVisible()
    chained = other.property("tone").name()
    assert chained in chains  # the file tree keeps its chain colour throughout

    app.controller.selectView("kobi", False)
    spin(until=lambda: halo.isVisible())
    ink = frame.property("tone").name()
    assert ink == QColor(COLORS["text"]).name() and ink not in chains
    assert frame.property("strokeWidth") == 1.5  # #201: no thicker border; the floor's footprint
    # the halo rings the frame outside its stroke, soft: the ink at low alpha
    hx, hy, hw, hh = window_rect(halo)
    fx, fy, fw, fh = window_rect(frame)
    assert hx < fx and hy < fy and hx + hw > fx + fw and hy + hh > fy + fh
    ring = halo.property("ring")
    assert ring.name() == ink and 0 < ring.alphaF() < 0.5
    assert other.property("tone").name() == chained
    assert not find_item(root, "halo:fege").isVisible()

    app.controller.clearSelection()
    spin(until=lambda: not halo.isVisible())


def test_a_maximised_view_keeps_its_header_buttons_clear_of_the_floating_controls(app, spin):
    add_gallery(app, spin)
    app.controller.maximize("kobi")
    root = app.window.contentItem()
    area = find_item(root, "canvasArea")
    slot = find_item(root, "view:kobi")
    spin(until=lambda: slot.width() == area.width() - 24)
    controls = window_rect(find_item(root, "canvasControls"))
    header = window_rect(find_item(root, "header:kobi"))
    assert header[1] >= controls[1] + controls[3]  # the header starts under the controls
    assert window_rect(slot)[1] + slot.height() == area.height() - 12
    app.controller.maximize("")
