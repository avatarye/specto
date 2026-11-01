"""Seed the project folder for the v2 #42 scripted flow: the first N cam-a and cam-b frames
of run-001 (jpg + json sidecar) under runs/run-001/, plus the cam-a video and its
``cam-a.timeline.json`` sidecar under runs/run-001/video/. No ``.specto/``: the app
initialises the project on open. An existing folder keeps everything but runs/run-001,
so a project opened empty fills in place (its watched file tree picks the files up).

    uv run python tools/gui_flows/v2-42-seed.py <folder> [--cam-a 120] [--cam-b 120]
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("v2_39_seed", HERE / "v2-39-seed.py")
_seed = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_seed)
TESTDATA, copy_frames = _seed.TESTDATA, _seed.copy_frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--cam-a", type=int, default=120)
    ap.add_argument("--cam-b", type=int, default=120)
    ns = ap.parse_args()
    root = ns.folder
    # only the seeded run is replaced: a folder already opened as a project (its
    # .specto/, the user's other files) is populated, not wiped
    if (root / "runs" / "run-001").exists():
        shutil.rmtree(root / "runs" / "run-001")
    front = copy_frames(root / "runs" / "run-001" / "cam-a", ns.cam_a, "cam-a")
    rear = copy_frames(root / "runs" / "run-001" / "cam-b", ns.cam_b, "cam-b")
    vdir = root / "runs" / "run-001" / "video"
    vdir.mkdir(parents=True)
    for name in ("cam-a.mp4", "cam-a.timeline.json"):
        src = TESTDATA / "runs" / "run-001" / "video" / name
        if not src.exists():
            print(f"[ERROR] no {src}; run tools/make_testdata.py --video-only")
            return 1
        shutil.copy2(src, vdir / name)
    print(f"[OK] seeded {root}: {len(front)} cam-a, {len(rear)} cam-b frames, cam-a.mp4")
    return 0


if __name__ == "__main__":
    sys.exit(main())
