"""Identicons (#79, #81, ADR 0018; glossary: identicon): a GitHub-style 5x5 horizontally
mirrored grid generated from a hash of a view's name, coloured by the view's link chain
so related views read as a group. Ports show them (#81): an input circle shows the
identicon of the upstream view, the output circle the view's own.

The grid itself is ``specto_core.identicon`` (Qt-free, shared with name generation so
no two names on a canvas are near twins). ``IdenticonProvider`` serves
``image://identicon/<name>?chain=<n>[&round=1]``: cells in the chain's theme token
(``chain_<n mod 6>``) on its soft tone, or the neutral ``chain_none`` on ``surface_alt``
without a chain; ``round`` is the port circles' image: a disc of the ground with the
whole grid inset in it, smaller, so no corner cell is cut (#92). Every
identicon meets one contrast floor (``CONTRAST_FLOOR``, WCAG 3:1, the non-text minimum)
against its ground: a cell colour that falls short is darkened, same hue, until it
clears it (review of #79: pale cells on a pale ground were unreadable at port size).
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider

from specto_core.identicon import GRID, identicon_cells

from ..theme import COLORS
from .chains import N_CHAINS

PROVIDER_ID = "identicon"
DEFAULT_PX = 40
#: the least WCAG contrast ratio between the cells and their ground
CONTRAST_FLOOR = 3.0
#: the share of the round image's inscribed square left free around the grid (#92)
INSET_MARGIN = 0.08


def _rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _luminance(color: str) -> float:
    def channel(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """The WCAG contrast ratio between two ``#rrggbb`` colours, 1 to 21."""
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def ensure_contrast(fg: str, bg: str, floor: float = CONTRAST_FLOOR) -> str:
    """``fg`` darkened (same hue) until it is at least ``floor`` against ``bg``."""
    r, g, b = _rgb(fg)
    while contrast_ratio(f"#{r:02x}{g:02x}{b:02x}", bg) < floor and (r, g, b) != (0, 0, 0):
        r, g, b = (max(0, int(v * 0.94)) for v in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def identicon_url(name: str, chain: int | None, *, round: bool = False) -> str:
    """What QML binds as ``source``; ``chain`` is the view's chain number or None,
    ``round`` asks for the circular image the port circles show."""
    query = [] if chain is None else [f"chain={int(chain)}"]
    if round:
        query.append("round=1")
    return f"image://{PROVIDER_ID}/{name}" + ("?" + "&".join(query) if query else "")


def parse_image_id(image_id: str) -> tuple[str, int | None, bool]:
    """``"kobi?chain=2&round=1"`` -> ``("kobi", 2, True)``; a missing or unreadable
    chain is None."""
    name, _, query = image_id.partition("?")
    chain: int | None = None
    is_round = False
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key == "chain":
            try:
                chain = int(value)
            except ValueError:
                chain = None
        elif key == "round":
            is_round = value not in ("", "0", "false")
    return name, chain, is_round


def identicon_colors(chain: int | None, colors: Mapping[str, str] = COLORS) -> tuple[str, str]:
    """``(cell colour, background)`` for a chain number: the chain token on its soft tone,
    or the neutral token on the alternate surface for a view in no chain; the cell colour
    darkened where the theme's pair falls short of ``CONTRAST_FLOOR``."""
    if chain is None or chain < 0:
        fg, bg = colors["chain_none"], colors["surface_alt"]
    else:
        token = f"chain_{chain % N_CHAINS}"
        fg, bg = colors[token], colors[f"{token}_soft"]
    return ensure_contrast(fg, bg), bg


def _paint_cells(painter: QPainter, name: str, x0: float, y0: float, cell: float) -> None:
    for r, row in enumerate(identicon_cells(name)):
        for c, on in enumerate(row):
            if on:
                painter.drawRect(QRectF(x0 + c * cell, y0 + r * cell, cell, cell))


def inset_cell(px: int) -> int:
    """The whole-pixel cell size of the grid inset in a ``px`` disc: the grid fits the
    disc's inscribed square less ``INSET_MARGIN`` of it, so no corner cell is cut."""
    return max(1, int(px / math.sqrt(2) * (1 - INSET_MARGIN) / GRID))


def render_identicon(
    name: str,
    chain: int | None,
    px: int,
    colors: Mapping[str, str] = COLORS,
    *,
    round: bool = False,
) -> QImage:
    """The identicon at ``px`` square, crisp cells (no antialiasing). ``round`` is the
    port circle's (#92): a disc of the identicon's ground (transparent corners) with the
    whole grid inset, smaller, inside the disc's inscribed square."""
    px = max(GRID, int(px))
    fg, bg = identicon_colors(chain, colors)
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(img)
    painter.setPen(Qt.PenStyle.NoPen)
    if round:
        img.fill(Qt.GlobalColor.transparent)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor(bg))
        painter.drawEllipse(QRectF(0, 0, px, px))
        cell = inset_cell(px)
        x0 = (px - cell * GRID) // 2
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(QColor(fg))
        _paint_cells(painter, name, x0, x0, cell)
    else:
        img.fill(QColor(bg))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(QColor(fg))
        _paint_cells(painter, name, 0, 0, px / GRID)
    painter.end()
    return img


class IdenticonProvider(QQuickImageProvider):
    """``image://identicon/<name>?chain=<n>[&round=1]``, cached per request."""

    def __init__(self, colors: Mapping[str, str] = COLORS) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.colors = colors
        self._cache: dict[tuple[str, int | None, int, bool], QImage] = {}
        self._lock = threading.Lock()

    def requestImage(self, image_id: str, size: QSize, requested: QSize) -> QImage:
        name, chain, is_round = parse_image_id(image_id)
        px = requested.width() if requested.isValid() and requested.width() > 0 else DEFAULT_PX
        key = (name, None if chain is None else chain % N_CHAINS, px, is_round)
        with self._lock:
            img = self._cache.get(key)
            if img is None:
                img = render_identicon(name, chain, px, self.colors, round=is_round)
                self._cache[key] = img
        try:
            size.setWidth(img.width())
            size.setHeight(img.height())
        except Exception:
            pass
        return img


__all__ = [
    "CONTRAST_FLOOR",
    "DEFAULT_PX",
    "GRID",
    "INSET_MARGIN",
    "PROVIDER_ID",
    "IdenticonProvider",
    "contrast_ratio",
    "ensure_contrast",
    "identicon_cells",
    "identicon_colors",
    "identicon_url",
    "inset_cell",
    "parse_image_id",
    "render_identicon",
]
