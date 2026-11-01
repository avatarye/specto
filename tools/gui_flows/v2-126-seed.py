"""Seed the project folder for the v2 #126 scripted flow (the view header standard and the
native views' footers): the test data's cam-a frames, two generated pdfs and a note.
No ``.specto/``: the app initialises the project on open.

    uv run python tools/gui_flows/v2-126-seed.py <folder>

- runs/cam-a/   twelve cam-a frames and their json sidecars (gallery, image, preview)
- papers/report.pdf   six A4 pages; papers/slides.pdf three landscape A5 pages (pdf)
- notes/readme.md
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication, QPageSize

HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    QGuiApplication(sys.argv[:1])  # QPdfWriter needs the native font database
    frames, pdfs = _load("v2-39-seed"), _load("v2-69-seed")
    frames.copy_frames(root / "runs" / "cam-a", 12, "cam-a")
    papers = root / "papers"
    papers.mkdir(parents=True)
    pdfs.write_pdf(papers / "report.pdf", "Report", 6, QPageSize.PageSizeId.A4, False)
    pdfs.write_pdf(papers / "slides.pdf", "Slides", 3, QPageSize.PageSizeId.A5, True)
    (root / "notes").mkdir()
    (root / "notes" / "readme.md").write_text("# run notes\n", encoding="utf-8")
    print(f"[OK] seeded {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
