"""SPEC-41: ``shell.locate`` (and the test driver's locate) answers the item tree as the
next frame draws it. A ListView applies a model's row changes at its next polish, so
between a row's removal and that polish its delegate is still in the item tree, visible;
the offscreen ``search`` flow read the search tree there 2 runs in 5."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QMetaObject, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickView

from specto_app.app import find_item, find_settled

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


def test_a_removed_rows_delegate_is_gone_before_the_next_frame(view):
    win, root = view
    assert find_settled(win, "row:a") is not None
    QMetaObject.invokeMethod(root, "dropFirst", Qt.ConnectionType.DirectConnection)
    # no event loop between the removal and the look: the list has not polished yet
    assert find_item(win.contentItem(), "row:a") is not None  # what the flow saw
    assert find_settled(win, "row:a") is None
    assert find_settled(win, "row:b") is not None
