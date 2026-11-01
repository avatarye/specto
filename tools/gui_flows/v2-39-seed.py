"""Seed the project folder for the v2 #39 scripted flow: the first N cam-a and cam-b
frames of run-001 (jpg + json sidecar) under runs/run-001/, plus a notes/ folder. No
``.specto/``: the app initialises the project on open.

    uv run python tools/gui_flows/v2-39-seed.py <folder> [--cam-a 12] [--cam-b 8]
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

TESTDATA = Path(os.environ.get("SPECTO_TESTDATA", Path(__file__).resolve().parents[2] / "testdata"))


def copy_frames(dst: Path, n: int, series: str) -> list[str]:
    src = TESTDATA / "runs" / "run-001" / series
    dst.mkdir(parents=True, exist_ok=True)
    names = []
    for j in sorted(src.glob("*.jpg"))[:n]:
        shutil.copy2(j, dst / j.name)
        side = j.with_suffix(".json")
        if side.exists():
            shutil.copy2(side, dst / side.name)
        names.append(j.name)
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--cam-a", type=int, default=12)
    ap.add_argument("--cam-b", type=int, default=8)
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    front = copy_frames(root / "runs" / "run-001" / "cam-a", ns.cam_a, "cam-a")
    rear = copy_frames(root / "runs" / "run-001" / "cam-b", ns.cam_b, "cam-b")
    (root / "notes").mkdir(parents=True, exist_ok=True)
    (root / "notes" / "readme.md").write_text("# run-001 review\n", encoding="utf-8")
    print(f"[OK] seeded {root}: {len(front)} cam-a, {len(rear)} cam-b frames")
    print("[OK] cam-a names: " + ", ".join(front))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
