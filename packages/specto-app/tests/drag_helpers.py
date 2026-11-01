"""Drags out of views, offscreen (#185). The offscreen platform has no drag loop
(``QDrag.exec`` returns at once), so :func:`drags_into` replaces the exec step
(``specto_app.drag.qt.set_runner``) with a delivery into a test drop target: a shown
``QQuickView`` holding a QML ``DropArea`` that records what it was given, the way the
scratch pad (#184) reads it."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent, QGuiApplication
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickView

from specto_app.drag.payload import SPECTO_MIME
from specto_app.drag.qt import set_runner

DROP_QML = b"""
import QtQuick
Item {
    width: 300; height: 200
    property var drops: []
    property bool entered: false
    DropArea {
        anchors.fill: parent
        onEntered: (drag) => { parent.entered = true; drag.accept(Qt.CopyAction) }
        onDropped: (drop) => {
            var d = { formats: [], text: drop.hasText ? drop.text : null,
                      html: drop.hasHtml ? drop.html : null, urls: drop.urls.map(String),
                      specto: drop.getDataAsString("application/x-specto-item+json"),
                      png: drop.getDataAsArrayBuffer("image/png").byteLength }
            parent.drops = parent.drops.concat([d])
            drop.accept(Qt.CopyAction)
        }
    }
}
"""


class DropTarget:
    """A window with a QML ``DropArea`` filling it; ``drops`` are what it was given."""

    def __init__(self) -> None:
        self.view = QQuickView()
        self.comp = QQmlComponent(self.view.engine())
        self.comp.setData(DROP_QML, QUrl())
        assert self.comp.status() == QQmlComponent.Status.Ready, self.comp.errorString()
        root = self.comp.create()
        assert isinstance(root, QQuickItem)
        root.setParentItem(self.view.contentItem())
        self.root = root
        self.view.resize(300, 200)
        self.view.setPosition(2000, 0)
        self.view.show()
        QGuiApplication.processEvents()

    @property
    def drops(self) -> list[dict[str, Any]]:
        v = self.root.property("drops")
        v = v.toVariant() if hasattr(v, "toVariant") else v
        return list(v or [])

    def specto(self, i: int = -1) -> dict[str, Any]:
        return json.loads(self.drops[i]["specto"])

    def close(self) -> None:
        self.view.close()
        self.root.deleteLater()
        self.view.deleteLater()


def deliver(drag: QDrag, target: DropTarget) -> Qt.DropAction:
    """What the platform's drag loop does once the pointer reaches the target's middle
    and the button comes up: enter, move, drop."""
    md = drag.mimeData()
    p = QPointF(150, 100)
    left, none = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    copy = Qt.DropAction.CopyAction
    events = (
        QDragEnterEvent(p, copy, md, left, none),
        QDragMoveEvent(p, copy, md, left, none),
        QDropEvent(p, copy, md, left, none),
    )
    action = Qt.DropAction.IgnoreAction
    for ev in events:
        QGuiApplication.sendEvent(target.view, ev)
        if ev.type() == QDropEvent.Type.Drop and ev.isAccepted():
            action = ev.dropAction()
    return action


@contextmanager
def drags_into(target: DropTarget) -> Iterator[list[QDrag]]:
    """While open, every drag a view starts lands in ``target``; yields the drags."""
    started: list[QDrag] = []

    def run(drag: QDrag) -> Qt.DropAction:
        started.append(drag)
        return deliver(drag, target)

    previous = set_runner(run)
    try:
        yield started
    finally:
        set_runner(previous)


def formats(drag: QDrag) -> list[str]:
    return list(drag.mimeData().formats())


def specto_of(drag: QDrag) -> dict[str, Any]:
    return json.loads(bytes(drag.mimeData().data(SPECTO_MIME).data()).decode("utf-8"))
