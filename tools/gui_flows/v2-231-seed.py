"""Seed the project folder for the v2 #231 scripted flow (a lazy tree): one big folder
of many subfolders, each holding many files, next to a small one, so opening the big
folder shows that only its own level is listed and nothing under it is walked. No
``.specto/``: the app initialises the project on open.

    uv run python tools/gui_flows/v2-231-seed.py <folder> [--folders 400] [--files 25]
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--folders", type=int, default=400)
    ap.add_argument("--files", type=int, default=25)
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    big = root / "big"
    for i in range(ns.folders):
        d = big / f"item_{i:04d}"
        d.mkdir(parents=True)
        for n in range(ns.files):
            (d / f"file_{n:02d}.txt").write_bytes(b"")
    (big / "index.txt").write_text("the big folder\n", encoding="utf-8")
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "readme.md").write_text("# notes\n", encoding="utf-8")
    total = ns.folders * ns.files + 2
    print(f"[OK] seeded {root}: big/ with {ns.folders} folders of {ns.files} files ({total} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
