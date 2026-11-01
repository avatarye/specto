"""Seed the project folder for the v2 #93 scripted flow: a copy of a page
whose panes are remote map frames. No ``.specto/``:
the app initialises the project on open.

    uv run python tools/gui_flows/v2-93-seed.py <folder> [--page <html>]

- pages/remote-frames.html   the page (and its pack.json, when there)

The page is yours (``SPECTO_V93_PAGE``, else ``~/pages/remote-frames.html``);
nothing here names a real one.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PAGE = Path(os.environ.get("SPECTO_V93_PAGE") or (Path.home() / "pages" / "remote-frames.html"))
#: the page's name in the project (the flow double-clicks it)
NAME = "remote-frames.html"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--page", type=Path, default=PAGE, help="the html page to copy")
    a = ap.parse_args()
    if not a.page.is_file():
        print(f"[ERROR] no page at {a.page} (SPECTO_V93_PAGE or --page)", file=sys.stderr)
        return 1
    dest = a.folder / "pages"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(a.page, dest / NAME)
    pack = a.page.with_name("pack.json")
    if pack.is_file():
        shutil.copy2(pack, dest / pack.name)
    print(f"[OK] seeded {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
