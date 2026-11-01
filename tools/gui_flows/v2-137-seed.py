"""Seed the project folder for the v2 #137 scripted flow (the usd view rendering with
RTX): worlds/depot_world.usda is a root layer that sublayers a large world from the
user's depot (``SPECTO_P4_DEPOT`` / ``SPECTO_P4_WORLD``, see ``_depot.py``), read in
place (its payloads are relative to it, so it cannot be copied), with the file's own
defaultPrim, metersPerUnit and upAxis. No ``.specto/``: the app initialises the project
on open.

    uv run python tools/gui_flows/v2-137-seed.py <folder>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _depot import depot_world

WORLD = depot_world()
#: the root layer's name in the project (the flows pick it in the file tree)
ROOT_NAME = "depot_world.usda"

ROOT = """#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
    subLayers = [@{world}@]
)
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    root = ap.parse_args().folder
    if not WORLD.is_file():
        print(f"[ERROR] the depot world is not on this machine: {WORLD} (SPECTO_P4_DEPOT, SPECTO_P4_WORLD)")
        return 1
    (root / "worlds").mkdir(parents=True, exist_ok=True)
    (root / "worlds" / ROOT_NAME).write_text(
        ROOT.format(world=WORLD.as_posix()), encoding="utf-8"
    )
    print(f"[OK] seeded {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
