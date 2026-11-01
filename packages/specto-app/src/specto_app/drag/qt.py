"""The one place a drag out of a view starts (#185): :func:`start_drag` turns a
:class:`~specto_app.drag.payload.DragPayload` into ``QMimeData`` (:func:`mime_data`), gives
the drag a picture (:func:`drag_pixmap`: the crop, the image's own thumbnail, or a label
chip) and runs ``QDrag.exec`` (copy). The platform runs the drag from there: Qt views,
the scratch pad and outside apps read the same mime data.

The view decides *when* (the platform's drag distance with the left button held, see
``docs/contracts.md`` section 11) and *what* (its ``drag_payload``); hosts reach this
through ``QuickViewHost.start_drag``. Tests replace the exec step with
:func:`set_runner` (the offscreen platform has no drag loop) and deliver the drag into a
drop target themselves.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import QByteArray, QMimeData, QObject, QPoint, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QDrag,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QImageReader,
    QPainter,
    QPainterPath,
    QPixmap,
)

from .payload import HTML_MIME, TEXT_MIME, URI_MIME, DragPayload

#: the drag picture's longest side (px)
PICTURE_PX = 128
#: image files above this are not decoded for the drag picture (the chip stands in)
PICTURE_MAX_BYTES = 64 * 1024 * 1024

Runner = Callable[[QDrag], Qt.DropAction]


def _exec(drag: QDrag) -> Qt.DropAction:
    return drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)


_runner: Runner = _exec


def set_runner(runner: Runner | None) -> Runner:
    """Replace the step that runs a started drag (None: the platform's ``QDrag.exec``);
    returns the one it replaced."""
    global _runner
    previous, _runner = _runner, runner or _exec
    return previous


def mime_data(payload: DragPayload) -> QMimeData:
    """The payload's formats as ``QMimeData``: the URLs, the text and the html through
    Qt's own setters (so the platform converts them for outside apps: file drops, plain
    and rich text), the picture also as image data."""
    md = QMimeData()
    for fmt, data in payload.formats().items():
        if fmt == URI_MIME:
            md.setUrls([QUrl(u) for u in payload.uris()])
        elif fmt == TEXT_MIME:
            md.setText(data.decode("utf-8"))
        elif fmt == HTML_MIME:
            md.setHtml(data.decode("utf-8"))
        else:
            md.setData(fmt, QByteArray(data))
    if payload.png:
        img = QImage.fromData(payload.png, "PNG")
        if not img.isNull():
            md.setImageData(img)
    return md


def drag_pixmap(payload: DragPayload) -> QPixmap:
    """What the pointer carries: the picture scaled down, a single image file's own
    thumbnail, else a chip with the payload's label."""
    img = QImage()
    if payload.png:
        img = QImage.fromData(payload.png, "PNG")
    elif len(payload.items) == 1 and payload.items[0].kind == "image":
        img = _thumbnail(payload.items[0].file)
    if not img.isNull():
        return QPixmap.fromImage(
            img.scaled(
                QSize(PICTURE_PX, PICTURE_PX),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
    return _chip(payload.label or "item")


def _thumbnail(path: str) -> QImage:
    try:
        if not path or os.path.getsize(path) > PICTURE_MAX_BYTES:
            return QImage()
    except OSError:
        return QImage()
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and max(size.width(), size.height()) > PICTURE_PX:
        reader.setScaledSize(
            size.scaled(PICTURE_PX, PICTURE_PX, Qt.AspectRatioMode.KeepAspectRatio)
        )
    return reader.read()


def _chip(text: str) -> QPixmap:
    font = QFont(QGuiApplication.font())
    fm = QFontMetrics(font)
    text = fm.elidedText(text, Qt.TextElideMode.ElideMiddle, 240)
    w, h = fm.horizontalAdvance(text) + 16, fm.height() + 8
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 6, 6)
    p.fillPath(path, QColor(32, 34, 38, 220))
    p.setPen(QColor(235, 237, 240))
    p.setFont(font)
    p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return QPixmap.fromImage(img)


def start_drag(
    source: QObject, payload: DragPayload, *, pixmap: QPixmap | None = None
) -> Qt.DropAction:
    """Start a copy drag of ``payload`` from ``source`` (the view's item); returns the
    action the drop target took (``IgnoreAction``: nothing took it)."""
    drag = QDrag(source)
    drag.setMimeData(mime_data(payload))
    pix = pixmap if pixmap is not None else drag_pixmap(payload)
    if not pix.isNull():
        drag.setPixmap(pix)
        drag.setHotSpot(QPoint(min(12, pix.width() // 2), min(12, pix.height() // 2)))
    return _runner(drag)


__all__ = ["PICTURE_PX", "Runner", "drag_pixmap", "mime_data", "set_runner", "start_drag"]
