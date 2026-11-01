"""Seed the project folder for the v2 #46 scripted flow: the first N cam-a frames of
run-001 (jpg + json sidecar) under runs/run-001/cam-a/, the notes folder (markdown with
wikilinks and an image) and the geo folder (GeoJSON) from the test data. No
``.specto/``: the app initialises the project on open.

    uv run python tools/gui_flows/v2-46-seed.py <folder> [--cam-a 12]
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--cam-a", type=int, default=12)
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    front = _seed.copy_frames(root / "runs" / "run-001" / "cam-a", ns.cam_a, "cam-a")
    for folder in ("notes", "geo"):
        src = _seed.TESTDATA / folder
        if src.is_dir():
            shutil.copytree(src, root / folder)
    print(f"[OK] seeded {root}: {len(front)} cam-a frames, notes/, geo/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
