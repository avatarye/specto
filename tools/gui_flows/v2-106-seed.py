"""Seed the project folder for the v2 #106 scripted flow: one file of each kind a preview
renders, plus one it does not, all in ``look/``, so one folder of the file tree holds
them all. Reuses the #95 pdf and html, the #47 point cloud and the test data's frame,
note, sidecar and video. No ``.specto/``: the app initialises the project on open.

    uv run python tools/gui_flows/v2-106-seed.py <folder>

- look/a-frame.jpg          a cam-a frame (image)
- look/b-report.pdf         six A4 pages covered by a coloured grid (pdf)
- look/c-notes.md           a test data note (markdown)
- look/d-cam-a.mp4          the cam-a video and its timeline (video)
- look/e-terrain.ply        a small point cloud (3D)
- look/f-report.html        a self-contained page with coloured blocks (html)
- look/g-sidecar.json       a frame's json sidecar (json)
- look/h-blob.bin           1.5 KB of bytes no renderer shows (the card)
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path

from PySide6.QtGui import QGuiApplication

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
    QGuiApplication([])  # QPdfWriter needs the native font database
    every, scans, frames = _load("v2-95-seed"), _load("v2-47-seed"), _load("v2-39-seed")
    look = root / "look"
    look.mkdir(parents=True)
    front = frames.TESTDATA / "runs" / "run-001" / "cam-a"
    first = sorted(front.glob("*.jpg"))[0]
    shutil.copy2(first, look / "a-frame.jpg")
    every.write_grid_pdf(look / "b-report.pdf", 6)
    shutil.copy2(frames.TESTDATA / "notes" / "run-001-summary.md", look / "c-notes.md")
    video = frames.TESTDATA / "runs" / "run-001" / "video"
    shutil.copy2(video / "cam-a.mp4", look / "d-cam-a.mp4")
    shutil.copy2(video / "cam-a.timeline.json", look / "d-cam-a.timeline.json")
    pts, rgb = scans.cloud(200_000)
    scans.write_ply_points(look / "e-terrain.ply", pts, rgb)
    (look / "f-report.html").write_text(every.HTML, encoding="utf-8")
    shutil.copy2(first.with_suffix(".json"), look / "g-sidecar.json")
    (look / "h-blob.bin").write_bytes(bytes(range(256)) * 6)
    print(f"[OK] seeded {root}: one file of each kind in look/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
