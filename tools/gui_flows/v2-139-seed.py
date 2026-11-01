"""Seed the project folder for the v2 #139 scripted flow (the usd view's chrome): two
small Z-up ``.usda`` worlds under worlds/ and a note. The usd view's source is the stub
USD source until #137, which serves its test pattern and a fake prim tree for any stage.
The folder is wiped first, so no ``.specto/`` from an earlier run (its canvas and the
tree's picks) survives: the flow starts with nothing picked. The app initialises the
project on open.

    uv run python tools/gui_flows/v2-139-seed.py <folder>
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

WORLD = """#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 0.01
    upAxis = "Z"
)

def Xform "World"
{
    def Cube "Block"
    {
        double size = 100
    }
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    root = ap.parse_args().folder
    if root.exists():
        shutil.rmtree(root)
    (root / "worlds").mkdir(parents=True, exist_ok=True)
    for name in ("depot_world", "second_world"):
        (root / "worlds" / f"{name}.usda").write_text(WORLD, encoding="utf-8")
    (root / "notes").mkdir(exist_ok=True)
    (root / "notes" / "readme.md").write_text("# Worlds\n\nTwo stub worlds.\n", encoding="utf-8")
    print(f"[OK] seeded {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
