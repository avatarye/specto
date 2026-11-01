"""The scratch pad in the shell (#184): the project node's double-click opens it beside the
file tree (its QML loads, the page's item in its slot under the tab strip), Ctrl+V pastes
into it, and one Ctrl+Z walks back the scratch
pad's steps and the canvas's in the order they happened (redo the same way). Offscreen
Qt with synthesised input; the real-mouse flow is tools/gui_flows/v2-184-scratch.txt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

pytest.importorskip("PySide6.QtQuick")

from specto_app.app import find_item
from specto_app.views.scratch_view import ScratchHost
from specto_core import gestures

LEFT = Qt.MouseButton.LeftButton
NOMOD = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier


def center(item):
    return item.mapToScene(item.boundingRect().center()).toPoint()


def open_pad(app, spin) -> str:
    node = find_item(app.window.contentItem(), "projectNode")
    QTest.mouseDClick(app.window, LEFT, NOMOD, center(node))
    spin(30)
    pads = [vid for vid, v in app.live.data["views"].items() if v["type"] == "scratch"]
    assert len(pads) == 1
    return pads[0]


def test_the_project_node_opens_the_pad_and_ctrl_v_pastes_into_it(app, spin):
    vid = open_pad(app, spin)
    assert app.live.history[-1].label == "open the scratch pad"
    host = app.hosts.host(vid)
    assert isinstance(host, ScratchHost) and host.item is not None
    pad = find_item(app.window.contentItem(), f"scratch:{vid}")
    assert pad is not None and pad.property("viewId") == vid
    # the page's web item sits in the slot under the tab strip, filling it
    slot = find_item(pad, f"scratchSlot:{vid}")
    assert slot is not None and slot.height() > 0
    if host._web_item is not None:
        assert host._web_item.parentItem() is slot
        assert host._web_item.height() == slot.height()
    # a second double-click brings the same pad, no second one
    assert open_pad(app, spin) == vid

    QGuiApplication.clipboard().setText("# Finding\nlidar drift at 00042")
    QTest.mouseClick(app.window, LEFT, NOMOD, center(find_item(pad, f"scratchTabs:{vid}")))
    spin(10)
    QTest.keyClick(app.window, Qt.Key.Key_V, CTRL)
    spin(30)
    assert app.engine.scratch.text() == "# Finding\nlidar drift at 00042\n"
    assert pad.property("tabs")[0]["count"] > 0


def test_one_ctrl_z_walks_both_stacks_newest_first(app, spin):
    vid = open_pad(app, spin)
    host = app.hosts.host(vid)
    QGuiApplication.clipboard().setText("first note")
    host.paste()
    spin(15)
    gestures.move_view(app.live, vid, (900.0, 40.0))  # a canvas step after the paste
    spin(15)
    QGuiApplication.clipboard().setText("second note")
    host.paste()
    c = app.controller
    assert "scratch pad: add second note" in c.lastTxnLabel
    assert c.undo() and app.engine.scratch.text() == "first note\n"
    assert c.lastTxnLabel.endswith("move scratch pad (user)")
    assert c.undo() and app.live.data["views"][vid]["layout"]["position"] != [900.0, 40.0]
    assert c.undo() and app.engine.scratch.text() == ""
    assert c.undo() and vid not in app.live.data["views"]  # the pad's opening
    # redo: the same way back
    assert c.redo() and vid in app.live.data["views"]
    assert c.redo() and app.engine.scratch.text() == "first note\n"
    assert c.redo() and app.live.data["views"][vid]["layout"]["position"] == [900.0, 40.0]
    assert c.redo() and app.engine.scratch.text() == "first note\n\nsecond note\n"
    assert not c.canRedo


def test_the_tab_strip_adds_and_switches_pages(app, spin):
    vid = open_pad(app, spin)
    pad = find_item(app.window.contentItem(), f"scratch:{vid}")
    store = app.engine.scratch
    assert find_item(pad, f"scratchTab:{vid}:p1") is not None
    QTest.mouseClick(app.window, LEFT, NOMOD, center(find_item(pad, f"scratchTabAdd:{vid}")))
    spin(until=lambda: find_item(pad, f"scratchTab:{vid}:p2") is not None)
    assert store.active == "p2" and pad.property("page") == "p2"
    QTest.mouseClick(app.window, LEFT, NOMOD, center(find_item(pad, f"scratchTab:{vid}:p1")))
    spin(until=lambda: store.active == "p1")
    assert app.live.data["views"][vid]["params"]["page"] == "p1"
    # Ctrl+Z takes the page back (its adding was the newest step)
    assert app.controller.undo() and store.page_ids() == ["p1"]
