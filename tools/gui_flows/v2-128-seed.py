"""Seed the project folder for the #128 video footer flow: three copies of the run-001
cam-a video (each with its ``.timeline.json`` sidecar) under runs/run-001/video/, so the
player has a collection to step through ("1 / 3"). No ``.specto/``: the app initialises
the project on open.

    uv run python tools/gui_flows/v2-128-seed.py <folder>
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

TESTDATA = Path(os.environ.get("SPECTO_TESTDATA", Path(__file__).resolve().parents[2] / "testdata"))
NAMES = ("take-1", "take-2", "take-3")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ns = ap.parse_args()
    src = TESTDATA / "runs" / "run-001" / "video"
    if not (src / "cam-a.mp4").exists():
        print(f"[ERROR] no {src / 'cam-a.mp4'}; run tools/make_testdata.py")
        return 1
    vdir = ns.folder / "runs" / "run-001" / "video"
    if vdir.exists():
        shutil.rmtree(vdir)
    vdir.mkdir(parents=True)
    for name in NAMES:
        shutil.copy2(src / "cam-a.mp4", vdir / f"{name}.mp4")
        shutil.copy2(src / "cam-a.timeline.json", vdir / f"{name}.timeline.json")
    print(f"[OK] seeded {ns.folder}: {len(NAMES)} copies of cam-a.mp4")
    return 0


if __name__ == "__main__":
    sys.exit(main())
