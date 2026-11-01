"""Theme-tinted SVG icons for the shell (gui-styles B: real icons, not ASCII glyphs).

One small consistent line set lives in ``qml/Specto/Shell/icons/*.svg`` drawn with
``currentColor``; QML asks for ``image://icons/<name>/<rrggbb>`` and the provider renders
the file with that colour at the requested size. ``Icon.qml`` wraps it so a QML file
writes ``Icon { name: "link"; color: theme.colors.text_muted }`` and never a raw path.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer

from .hosts.base import SHELL_QML

PROVIDER_ID = "icons"
ICON_DIR = SHELL_QML / "icons"
DEFAULT_PX = 32


def icon_names(root: Path = ICON_DIR) -> list[str]:
    return sorted(p.stem for p in root.glob("*.svg"))


def render_icon(name: str, color: str, px: int, root: Path = ICON_DIR) -> QImage:
    """``name.svg`` tinted ``color`` (``#rrggbb`` or ``rrggbb``) at ``px`` square."""
    path = root / f"{name}.svg"
    px = max(4, int(px))
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    if not path.is_file():
        return img
    color = color if color.startswith("#") else "#" + color
    svg = path.read_text(encoding="utf-8").replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return img
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return img


class IconProvider(QQuickImageProvider):
    """``image://icons/<name>/<rrggbb>`` (the colour without ``#``: a fragment would be
    cut off the URL). Cached per (name, colour, size)."""

    def __init__(self, root: Path = ICON_DIR) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.root = root
        self._cache: dict[tuple[str, str, int], QImage] = {}
        self._lock = threading.Lock()

    def requestImage(self, image_id: str, size: QSize, requested: QSize) -> QImage:
        name, _, color = image_id.partition("/")
        color = color or "000000"
        px = requested.width() if requested.isValid() and requested.width() > 0 else DEFAULT_PX
        key = (name, color, px)
        with self._lock:
            img = self._cache.get(key)
            if img is None:
                img = render_icon(name, color, px, self.root)
                self._cache[key] = img
        try:
            size.setWidth(img.width())
            size.setHeight(img.height())
        except Exception:
            pass
        return img


__all__ = ["ICON_DIR", "PROVIDER_ID", "IconProvider", "icon_names", "render_icon"]
