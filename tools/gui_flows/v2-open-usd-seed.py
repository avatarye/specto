"""Seed the project folder for the SPEC-34 scripted flow (two stages opened in turn): two
small Z-up stages, a ``.usda`` under worlds/ and a second ``.usda`` under props/, and a
note. The folder is wiped first, so no ``.specto/`` from an earlier run survives. The app
initialises the project on open.

    uv run python tools/gui_flows/v2-open-usd-seed.py <folder>
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

STAGE = """#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 0.01
    upAxis = "Z"
)

def Xform "World"
{
    def Cube "Block"
    {
        double size = %d
    }
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    root = ap.parse_args().folder
    if root.exists():
        shutil.rmtree(root)
    for rel, size in (("worlds/yard.usda", 100), ("props/crate.usda", 40)):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(STAGE % size, encoding="utf-8")
    (root / "notes").mkdir(exist_ok=True)
    (root / "notes" / "readme.md").write_text("# Stages\n\nTwo small stages.\n", encoding="utf-8")
    print(f"[OK] seeded {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
