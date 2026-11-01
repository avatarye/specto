"""Seed the project folder for the v2 #184 scratch pad flow: the #46 project (cam-a
frames, notes/, geo/; tools/gui_flows/v2-46-seed.py) plus pics/red.png, the picture the
scripted agent keeps. No ``.specto/``: the app initialises the project on open.

    uv run python tools/gui_flows/v2-184-seed.py <folder>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: v2-184-seed.py <folder>")
        return 2
    root = Path(sys.argv[1])
    subprocess.run([sys.executable, str(HERE / "v2-46-seed.py"), str(root)], check=True)
    pics = root / "pics"
    pics.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), (220, 40, 40)).save(pics / "red.png")
    print(f"[OK] seeded {root}: + pics/red.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
