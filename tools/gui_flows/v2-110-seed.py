"""Seed the project folder for the v2 #110 scripted flow: a deep tree of empty notes,
``vault/area_NN/topic_X/note_N.md`` (the vault opens on load, the areas stay closed),
taller than the file tree so it scrolls. No ``.specto/``: the app initialises the
project on open.

    uv run python tools/gui_flows/v2-110-seed.py <folder> [--areas 80]
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--areas", type=int, default=80)
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    for i in range(ns.areas):
        for topic in "abc":
            d = root / "vault" / f"area_{i:02d}" / f"topic_{topic}"
            d.mkdir(parents=True)
            for n in range(3):
                (d / f"note_{n}.md").write_bytes(b"")
    print(f"[OK] seeded {root}: {ns.areas} areas of 3 topics of 3 notes under vault/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
