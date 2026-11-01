"""The clipboard the app reads and writes: the system's through ``QGuiApplication``,
or, under the test driver (``specto_app.testdrive``, #237), an in-memory ``QMimeData``
that never touches the user's own. Every clipboard access of the app goes through here
(the context menu's Copy path, the panel's Copy, the prompt's and the scratch pad's
paste), so a driver run leaves the real clipboard as it found it.
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QGuiApplication

_fake: QMimeData | None = None
_use_fake = False


def use_fake(on: bool) -> None:
    """Route the app's clipboard to an in-memory store (the test driver)."""
    global _fake, _use_fake
    _use_fake = on
    _fake = QMimeData() if on else None


def is_fake() -> bool:
    return _use_fake


def set_text(text: str) -> None:
    if _use_fake:
        md = QMimeData()
        md.setText(str(text))
        _set_fake(md)
        return
    QGuiApplication.clipboard().setText(str(text))


def set_mime_data(md: QMimeData) -> None:
    if _use_fake:
        _set_fake(md)
        return
    QGuiApplication.clipboard().setMimeData(md)


def text() -> str:
    md = mime_data()
    return md.text() if md is not None and md.hasText() else ""


def mime_data() -> QMimeData | None:
    if _use_fake:
        return _fake
    return QGuiApplication.clipboard().mimeData()


def clear() -> None:
    if _use_fake:
        _set_fake(QMimeData())
        return
    QGuiApplication.clipboard().clear()


def _set_fake(md: QMimeData) -> None:
    global _fake
    _fake = md


__all__ = ["clear", "is_fake", "mime_data", "set_mime_data", "set_text", "text", "use_fake"]
