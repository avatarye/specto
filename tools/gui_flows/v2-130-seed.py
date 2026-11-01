"""Seed the project folder for the v2 #130 scripted flow (web view chrome): the #46
seed (12 cam-a frames with their JSON sidecars, the notes, the GeoJSON) plus a two-page
html report under report/ with its stylesheet. No ``.specto/``: the app initialises the
project on open.

    uv run python tools/gui_flows/v2-130-seed.py <folder>
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("v2_39_seed", HERE / "v2-39-seed.py")
_seed = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_seed)

STYLE = """body { font: 15px 'Segoe UI', sans-serif; margin: 0; color: #3a3630; }
header { background: #4a7fb5; color: #fff; padding: 12px 16px; font-size: 20px; font-weight: 600; }
.bar { margin: 14px 16px; height: 52px; border-radius: 4px; }
.a { background: #c2783a; } .b { background: #5f9e6e; }
p { margin: 14px 16px; }
"""

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="style.css"><title>{title}</title></head>
<body><header>{title}</header><div class="bar a"></div><div class="bar b"></div>
<p>{text}</p></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    front = _seed.copy_frames(root / "runs" / "run-001" / "cam-a", 12, "cam-a")
    for folder in ("notes", "geo"):
        src = _seed.TESTDATA / folder
        if src.is_dir():
            shutil.copytree(src, root / folder)
    report = root / "report"
    report.mkdir(parents=True)
    (report / "style.css").write_text(STYLE, encoding="utf-8", newline="\n")
    (report / "report.html").write_text(
        PAGE.format(title="HTML report", text="run-001 cam-a, 12 frames."),
        encoding="utf-8",
        newline="\n",
    )
    (report / "summary.html").write_text(
        PAGE.format(title="Summary", text="The second page of the report."),
        encoding="utf-8",
        newline="\n",
    )
    print(f"[OK] seeded {root}: {len(front)} cam-a frames, notes/, geo/, report/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
