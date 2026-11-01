"""PDF fixtures for the pdf view tests: small multi-page files written by QPdfWriter
(generated, never downloaded), each page carrying its number in large type."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPainter, QPdfWriter


def make_pdf(path: Path, pages: int = 3, *, title: str = "") -> Path:
    """Write ``pages`` A5 pages to ``path``; page i reads "<title> page i"."""
    path.parent.mkdir(parents=True, exist_ok=True)
    w = QPdfWriter(str(path))
    w.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A5),
            QPageLayout.Orientation.Portrait,
            QMarginsF(0, 0, 0, 0),
        )
    )
    w.setResolution(72)
    if title:
        w.setTitle(title)
    p = QPainter(w)
    p.setFont(QFont("Arial", 36))
    for i in range(pages):
        if i:
            w.newPage()
        p.drawText(40, 80, f"{title or path.stem} page {i + 1}")
    p.end()
    return path
