"""Seed the project folder for the v2 #97 scripted flow: a deep tree, the first N front
frames of run-001 five folders down (runs/run-001/2026-10-18/session_04/cam-a-wide/),
with a long-named report beside them, plus notes/readme.md dated 2024. No ``.specto/``:
the app initialises the project on open.

    uv run python tools/gui_flows/v2-97-seed.py <folder> [--cam-a 8]
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime
from pathlib import Path

TESTDATA = Path(os.environ.get("SPECTO_TESTDATA", Path(__file__).resolve().parents[2] / "testdata"))
DEEP = Path("runs") / "run-001" / "2026-10-18" / "session_04" / "cam-a-wide"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--cam-a", type=int, default=8)
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    deep = root / DEEP
    deep.mkdir(parents=True)
    src = TESTDATA / "runs" / "run-001" / "cam-a"
    for j in sorted(src.glob("*.jpg"))[: ns.cam_a]:
        shutil.copy2(j, deep / j.name)
        if j.with_suffix(".json").exists():
            shutil.copy2(j.with_suffix(".json"), deep / j.with_suffix(".json").name)
    (deep / "cam_a_wide_exposure_report_final_v3.md").write_text(
        "# Exposure\n", encoding="utf-8"
    )
    (root / "notes").mkdir()
    readme = root / "notes" / "readme.md"
    readme.write_text("# run-001 review\n", encoding="utf-8")
    t = datetime(2024, 3, 4, 15, 6).timestamp()
    os.utime(readme, (t, t))
    print(f"[OK] seeded {root}: {ns.cam_a} cam-a frames under {DEEP.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
