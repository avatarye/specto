"""Seed the project folder for the v2 #115 scripted flow: notes of four ages (2 min,
25 min, 5 h, 20 days old: one per age colour) and frames under ``runs/cam-a/``, one with
a long name the file tree cuts in the middle. No ``.specto/``: the app initialises the
project on open (the file tree auto-updates). ``--arrive`` drops one more note into
``notes/`` while the app runs (the flow spawns it).

    uv run python tools/gui_flows/v2-115-seed.py <folder> [--arrive]
"""

from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path

TESTDATA = Path(os.environ.get("SPECTO_TESTDATA", Path(__file__).resolve().parents[2] / "testdata"))
FRONT = TESTDATA / "runs" / "run-001" / "cam-a"
LONG = "00002_frame_with_a_very_long_name_for_the_tree_7255.jpg"
NOTES = {  # name: seconds old
    "fresh-note.md": 2 * 60,
    "this-hour.md": 25 * 60,
    "earlier-today.md": 5 * 3600,
    "last-month.md": 20 * 86400,
}


def frame_bytes() -> bytes:
    frames = sorted(FRONT.glob("*.jpg")) if FRONT.exists() else []
    return frames[0].read_bytes() if frames else b"\xff\xd8\xff\xd9"


def touch(path: Path, age_s: float, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    t = time.time() - age_s
    os.utime(path, (t, t))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--arrive", action="store_true", help="drop notes/just-arrived.md")
    ns = ap.parse_args()
    root = ns.folder
    if ns.arrive:
        tmp = root / "notes" / "just-arrived.md.part"
        touch(tmp, 0, b"# arrived\n\nwritten while the app runs\n")
        tmp.replace(tmp.with_suffix(""))
        print(f"[OK] dropped {tmp.with_suffix('')}")
        return 0
    if root.exists():
        shutil.rmtree(root)
    for name, age in NOTES.items():
        touch(root / "notes" / name, age, f"# {name}\n".encode() * (40 * (1 + len(name))))
    jpg = frame_bytes()
    for i, name in enumerate(["00000.jpg", "00001.jpg", LONG, "00003.jpg"]):
        touch(root / "runs" / "cam-a" / name, 3 * 86400 + i * 60, jpg)
    print(f"[OK] seeded {root}: {len(NOTES)} notes of four ages, 4 frames (one long name)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
