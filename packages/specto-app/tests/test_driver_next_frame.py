"""SPEC-41: the test driver answers ``locate`` after the window's next frame, so it reads
the item tree that frame draws. A ListView applies a model's row changes at its next
polish; between a row's removal and that polish its delegate is still in the item tree,
visible, and the offscreen ``search`` flow read it there now and then. Nothing is
forced: a normal ``shell.locate`` (an agent's, through the SDK) polishes nothing."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QMetaObject, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickView

from specto_app.app import find_item
from specto_app.testdrive import NEXT_FRAME_OPS, NextSync

LIST_QML = b"""
import QtQuick
Item {
    width: 200; height: 200
    function dropFirst() { rows.remove(0) }
    ListView {
        anchors.fill: parent
        model: ListModel { id: rows; ListElement { name: "a" } ListElement { name: "b" } }
        delegate: Item { objectName: "row:" + name; width: 200; height: 20 }
    }
}
"""


@pytest.fixture
def view(qapp) -> Iterator[tuple[QQuickView, QQuickItem]]:
    view = QQuickView()
    comp = QQmlComponent(view.engine())
    comp.setData(LIST_QML, QUrl())
    assert comp.status() == QQmlComponent.Status.Ready, comp.errorString()
    root = comp.create()
    assert isinstance(root, QQuickItem)
    root.setParentItem(view.contentItem())
    view.resize(200, 200)
    view.show()
    for _ in range(20):
        QGuiApplication.processEvents()
    yield view, root
    view.close()
    root.deleteLater()
    view.deleteLater()


def _wait(wait: NextSync, seconds: float = 3.0) -> None:
    deadline = time.monotonic() + seconds
    while not wait.done and time.monotonic() < deadline:
        QGuiApplication.processEvents()
        time.sleep(0.002)
    assert wait.done


def test_after_the_next_frame_a_removed_rows_delegate_is_gone(view):
    win, root = view
    QMetaObject.invokeMethod(root, "dropFirst", Qt.ConnectionType.DirectConnection)
    # no event loop since the removal: the list has not polished yet (what the flow saw)
    assert find_item(win.contentItem(), "row:a") is not None
    seen: list[object] = []
    wait = NextSync(win, lambda: seen.append(find_item(win.contentItem(), "row:a")))
    _wait(wait)
    assert seen == [None] and not wait.timed_out  # a frame came, well before the timeout
    assert find_item(win.contentItem(), "row:b") is not None


def test_a_window_that_draws_no_frame_answers_after_the_timeout(view):
    win, _root = view
    win.hide()
    ran: list[float] = []
    t0 = time.monotonic()
    wait = NextSync(win, lambda: ran.append(time.monotonic() - t0), timeout_ms=100)
    _wait(wait)
    assert len(ran) == 1 and ran[0] >= 0.09 and wait.timed_out


def test_only_locate_waits_for_a_frame():
    assert frozenset({"locate"}) == NEXT_FRAME_OPS
