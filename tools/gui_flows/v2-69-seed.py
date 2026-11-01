"""Seed the project folder for the v2 #69 scripted flow: generated multi-page PDFs under
papers/ (QPdfWriter, nothing downloaded) plus notes/. No ``.specto/``: the app
initialises the project on open.

    uv run python tools/gui_flows/v2-69-seed.py <folder> [--pages 6]

- papers/report.pdf   ``--pages`` A4 pages, each with a big page number, a coloured band
                      and a paragraph, so scrolling and zooming are visible in shots
- papers/slides.pdf   three landscape pages (the second document the view switches to)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# the native platform, not offscreen: the offscreen font database has no real fonts, and
# QtPdf then renders every glyph of the embedded subset as a box
from PySide6.QtCore import QMarginsF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
)

BANDS = ["#3b6ea5", "#c25e3a", "#4f9a63", "#8a5fb5", "#c9a227", "#5a8f9f"]
LOREM = (
    "Specto shows the pages of this file in a pdf view: scroll with the wheel, page "
    "with PageDown, zoom with Ctrl+wheel, fit the width with F or the page with P."
)


def write_pdf(path: Path, title: str, pages: int, size: QPageSize.PageSizeId, landscape: bool):
    w = QPdfWriter(str(path))
    orient = QPageLayout.Orientation.Landscape if landscape else QPageLayout.Orientation.Portrait
    w.setPageLayout(QPageLayout(QPageSize(size), orient, QMarginsF(0, 0, 0, 0)))
    w.setResolution(72)
    w.setTitle(title)
    p = QPainter(w)
    pw, ph = w.width(), w.height()
    for i in range(pages):
        if i:
            w.newPage()
        p.fillRect(QRectF(0, 0, pw, 72), QColor(BANDS[i % len(BANDS)]))
        p.setPen(QColor("white"))
        p.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        p.drawText(QRectF(24, 0, pw - 48, 72), int(Qt.AlignmentFlag.AlignVCenter), f"{title}")
        p.setPen(QColor("#222222"))
        p.setFont(QFont("Arial", 160, QFont.Weight.Bold))
        p.drawText(QRectF(0, 90, pw, 220), int(Qt.AlignmentFlag.AlignCenter), f"{i + 1}")
        p.setFont(QFont("Arial", 14))
        p.drawText(
            QRectF(48, 340, pw - 96, ph - 400),
            int(Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            f"Page {i + 1} of {pages}. {LOREM}",
        )
    p.end()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--pages", type=int, default=6)
    ns = ap.parse_args()
    QGuiApplication(sys.argv[:1])
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    papers = root / "papers"
    papers.mkdir(parents=True)
    write_pdf(papers / "report.pdf", "Report", ns.pages, QPageSize.PageSizeId.A4, False)
    print(f"[OK] papers/report.pdf ({ns.pages} pages)")
    write_pdf(papers / "slides.pdf", "Slides", 3, QPageSize.PageSizeId.A5, True)
    print("[OK] papers/slides.pdf (3 landscape pages)")
    (root / "notes").mkdir()
    (root / "notes" / "readme.md").write_text("# papers review\n", encoding="utf-8")
    print(f"[OK] seeded {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
