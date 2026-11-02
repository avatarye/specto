"""Seed the project folder for the v2 #232 scripted flow (type-to-search): a few readme
files at three depths among frames and notes, so one word finds hits in folders the
collapsed tree has not listed, and a ``docs-list`` note outside ``docs/``, so the word
``docs`` has a name match to rank above the path matches (#36). No ``.specto/``: the
app initialises the project on open.

    uv run python tools/gui_flows/v2-232-seed.py <folder>
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

FILES = {
    "notes/readme.md": "# notes\n",
    "notes/plan.md": "# plan\n",
    "notes/docs-list.md": "# the docs\n",
    "docs/readme.txt": "docs\n",
    "docs/deep/er/readme-draft.md": "# draft\n",
    "docs/deep/er/other.md": "# other\n",
    "runs/run-001/cam-a/frame_000.json": "{}\n",
    "runs/run-001/cam-a/frame_001.json": "{}\n",
    "runs/run-001/cam-b/frame_000.json": "{}\n",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    print(f"[OK] seeded {root}: {len(FILES)} files, three readmes at three depths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
