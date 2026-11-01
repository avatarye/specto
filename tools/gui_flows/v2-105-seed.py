"""Seed the project folder for the v2 #105 scripted flow: a mixed collection under data/
(images, videos, pdfs, markdown, json, html and 3D stubs; tools/make_mixed_gallery.py,
generated, nothing downloaded). No ``.specto/``: the app initialises the project on open.

    uv run python tools/gui_flows/v2-105-seed.py <folder> [--count 60]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--count", type=int, default=60)
    ns = ap.parse_args()
    if ns.folder.exists():
        shutil.rmtree(ns.folder)
    gen = HERE.parent / "make_mixed_gallery.py"
    cmd = [sys.executable, str(gen), "--count", str(ns.count), "--out", str(ns.folder)]
    subprocess.run(cmd, check=True)
    shutil.rmtree(ns.folder / "_clips", ignore_errors=True)  # the clips live on in data/
    print(f"[OK] seeded {ns.folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
