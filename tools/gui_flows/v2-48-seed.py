"""Seed the project folder for the v2 #48 scripted flow: a ``drops/`` folder holding
the first two run-001 cam-a frames (jpg + json sidecar), which ``tools/drip_files.py``
then feeds while the app watches it, plus a notes/ folder. No ``.specto/``: the app
initialises the project on open.

    uv run python tools/gui_flows/v2-48-seed.py <folder> [--frames 2]
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

TESTDATA = Path(os.environ.get("SPECTO_TESTDATA", Path(__file__).resolve().parents[2] / "testdata"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--frames", type=int, default=2)
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    drops = root / "drops"
    drops.mkdir(parents=True)
    src = TESTDATA / "runs" / "run-001" / "cam-a"
    names = []
    for j in sorted(src.glob("*.jpg"))[: ns.frames]:
        shutil.copy2(j, drops / j.name)
        side = j.with_suffix(".json")
        if side.exists():
            shutil.copy2(side, drops / side.name)
        names.append(j.name)
    (root / "notes").mkdir()
    (root / "notes" / "readme.md").write_text("# drops\n", encoding="utf-8")
    print(f"[OK] seeded {root}: {len(names)} frames in drops/ ({', '.join(names)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
