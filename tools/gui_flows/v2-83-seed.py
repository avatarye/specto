"""Seed the project folder for the v2 #83 scripted flow: the #39 frames (cam-a and cam-b
of run-001, notes/) plus a canvas saved before the project source had its one output:
the file tree at the canvas origin fed from ``project.folder``, a cam-a gallery pinned
off the file tree's ``all_files`` and a cam-b gallery on the tree's picks. Opening it
rewires the pinned link to ``project.all_files`` and moves the layout right of the
project node, as one journal entry.

    uv run python tools/gui_flows/v2-83-seed.py <folder>
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEYS = r"(?P<frame>\d{5})_\d+\.jpg$"


def old_canvas(label: str) -> dict:
    return {
        "schema_version": 2,
        "project": {"label": label},
        "window_frame": {"rect": {"x": 0, "y": 0, "w": 1600, "h": 900}},
        "root_view": None,
        "views": {
            "files": {
                "type": "file_tree",
                "label": "Files",
                "layout": {"position": [0, 0], "size": [280, 900]},
            },
            "kobi": {
                "type": "gallery",
                "label": "cam-a run",
                "layout": {"position": [300, 0], "size": [640, 430]},
                "params": {"columns": 4},
            },
            "tamu": {
                "type": "gallery",
                "label": "picked frames",
                "layout": {"position": [300, 450], "size": [640, 430]},
                "params": {"columns": 4},
            },
        },
        "links": {
            "l1": {
                "kind": "intaker",
                "label": "project files",
                "source": {"view": "project", "port": "folder"},
                "target": {"view": "files", "port": "data"},
                "params": {"sources": ["."], "watch": True},
                "summary": {"sentence": "The file tree shows every file in the project folder."},
            },
            "l2": {
                "kind": "intaker",
                "scope": "pinned",
                "label": "cam-a frames",
                "source": {"view": "files", "port": "all_files"},
                "target": {"view": "kobi", "port": "data"},
                "params": {
                    "sources": ["runs/run-001/cam-a"],
                    "screener": ["@images"],
                    "keys": KEYS,
                    "group_by": ["frame"],
                },
                "summary": {"sentence": "The cam-a gallery always shows the cam-a run."},
            },
            "l3": {
                "kind": "intaker",
                "scope": "selection",
                "label": "picked frames",
                "source": {"view": "files", "port": "selected_files"},
                "target": {"view": "tamu", "port": "data"},
                "params": {"screener": ["@images"], "keys": KEYS, "group_by": ["frame"]},
                "summary": {"sentence": "The lower gallery shows the frames you pick."},
            },
        },
    }


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    subprocess.run(
        [sys.executable, str(HERE / "v2-39-seed.py"), str(root), "--cam-a", "12", "--cam-b", "8"],
        check=True,
    )
    from specto_core import Project

    proj = Project.init(root, root.name)
    proj.canvas_file.write_text(json.dumps(old_canvas(root.name), indent=1), encoding="utf-8")
    print(f"[OK] wrote a pre-#83 canvas to {proj.canvas_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
