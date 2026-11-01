"""Seed the project folder for the v2 #95 scripted flow: one file of every view type, so
one canvas holds a pdf, html, markdown, json, 3D, gallery, image and video view. Reuses
the #47 (3D) and #39 (frames) seeds; the video comes from the test data. No
``.specto/``: the app initialises the project on open.

    uv run python tools/gui_flows/v2-95-seed.py <folder>

- papers/report.pdf      six A4 pages covered by a coloured grid
- pages/report.html      a self-contained page with coloured blocks
- notes/                 the test data's markdown notes
- runs/run-001/cam-a/     12 cam-a frames with their json sidecars
- runs/run-001/video/     cam-a.mp4 and its timeline
- scans/terrain.ply      a small point cloud (v2-47-seed.py)
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path

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

HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Report</title>
<style>
body { font: 16px Arial, sans-serif; margin: 0; background: #fdfbf7; color: #222; }
header { background: #3b6ea5; color: white; padding: 18px 24px; font-size: 28px; }
.band { height: 90px; margin: 16px 24px; }
</style></head>
<body>
<header>HTML report</header>
<div class="band" style="background:#c25e3a"></div>
<p style="margin: 0 24px">Blocks of colour, so a blank view is easy to tell apart.</p>
<div class="band" style="background:#4f9a63"></div>
<div class="band" style="background:#8a5fb5"></div>
</body></html>
"""


def write_grid_pdf(path: Path, pages: int) -> None:
    """A4 pages covered edge to edge by a coloured grid with the page number on top: any
    part of a page that shows is painted, so a blank view is never a blank stretch of
    page."""
    w = QPdfWriter(str(path))
    portrait = QPageLayout.Orientation.Portrait
    w.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), portrait, QMarginsF()))
    w.setResolution(72)
    p = QPainter(w)
    pw, ph, step = w.width(), w.height(), 24
    for i in range(pages):
        if i:
            w.newPage()
        for y in range(0, ph, step):
            for x in range(0, pw, step):
                hue = (x * 300 // pw + i * 50) % 360
                light = 150 + 60 * (((x + y) // step) % 2)
                p.fillRect(QRectF(x, y, step, step), QColor.fromHsl(hue, 160, light))
        p.setPen(QColor("#222222"))
        p.setFont(QFont("Arial", 160, QFont.Weight.Bold))
        p.drawText(QRectF(0, 90, pw, 220), int(Qt.AlignmentFlag.AlignCenter), f"{i + 1}")
    p.end()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    QGuiApplication([])  # QPdfWriter needs the native font database
    scans, frames = _load("v2-47-seed"), _load("v2-39-seed")
    (root / "papers").mkdir(parents=True)
    write_grid_pdf(root / "papers" / "report.pdf", 6)
    (root / "pages").mkdir()
    (root / "pages" / "report.html").write_text(HTML, encoding="utf-8")
    shutil.copytree(frames.TESTDATA / "notes", root / "notes")
    frames.copy_frames(root / "runs" / "run-001" / "cam-a", 12, "cam-a")
    vdir = root / "runs" / "run-001" / "video"
    vdir.mkdir(parents=True)
    for name in ("cam-a.mp4", "cam-a.timeline.json"):
        shutil.copy2(frames.TESTDATA / "runs" / "run-001" / "video" / name, vdir / name)
    (root / "scans").mkdir()
    pts, rgb = scans.cloud(200_000)
    scans.write_ply_points(root / "scans" / "terrain.ply", pts, rgb)
    print(f"[OK] seeded {root}: pdf, html, notes, 12 frames, video, point cloud")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
