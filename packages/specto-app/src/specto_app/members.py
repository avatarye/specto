"""Images inside containers for QML ``Image`` (M2 containers): a virtual path
``drive.zip#/front/00042.jpg`` has no ``file:`` URL, so the hover preview, the quick
look and the full-size image view load it through ``image://member/<url-encoded path>``.

``MemberImageProvider`` reads the member by stream (``specto_core.containers``) on
Qt's image-loader thread, never on the GUI thread; ``image_url_for`` picks the right
URL for any item part. Registered on first use per QML engine.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, unquote

from PySide6.QtCore import QBuffer, QByteArray, QSize, Qt, QUrl
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtQml import QQmlEngine
from PySide6.QtQuick import QQuickImageProvider

from specto_core.containers import is_virtual, open_source

PROVIDER_ID = "member"
_installed: set[int] = set()


class MemberImageProvider(QQuickImageProvider):
    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, id: str, size: QSize, requested: QSize) -> QImage:
        path = unquote(id.split("?", 1)[0])
        try:
            with open_source(path) as fh:
                data = fh.read()
        except Exception:
            data = b""
        img = QImage()
        if data:
            # the byte array and the buffer must outlive the reader (it reads lazily)
            raw = QByteArray(data)
            buf = QBuffer(raw)
            buf.open(QBuffer.OpenModeFlag.ReadOnly)
            reader = QImageReader(buf)
            reader.setAutoTransform(True)
            if requested.isValid() and requested.width() > 0:
                src = reader.size()
                if src.isValid() and src.width() > requested.width():
                    reader.setScaledSize(src.scaled(requested, Qt.AspectRatioMode.KeepAspectRatio))
            img = reader.read()
            buf.close()
        if img.isNull():
            img = QImage(1, 1, QImage.Format.Format_RGBA8888)
            img.fill(0)
        size.setWidth(img.width())
        size.setHeight(img.height())
        return img


def ensure_provider(engine: QQmlEngine | None) -> None:
    if engine is None or id(engine) in _installed:
        return
    engine.addImageProvider(PROVIDER_ID, MemberImageProvider())
    _installed.add(id(engine))


def image_url_for(uri: str) -> str:
    """A QML ``Image`` source for an item part: ``file:`` for a plain path, the member
    provider for a virtual one, anything with a scheme as is."""
    if "://" in uri:
        return uri
    if is_virtual(uri):
        return f"image://{PROVIDER_ID}/{quote(uri.replace(chr(92), '/'), safe='')}"
    return QUrl.fromLocalFile(str(Path(uri))).toString()


__all__ = ["PROVIDER_ID", "MemberImageProvider", "ensure_provider", "image_url_for"]
