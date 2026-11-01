"""Seed the project folder for the v2 #43 scripted flow: the run-001 frames of the #39
seed (12 cam-a, 8 cam-b) plus the Python link file ``.specto/links/cam_a_to_cam_b.py``
that the flow approves by mouse. ``--edit`` rewrites that file (frame N -> cam-b frame
N+1) so the flow can show that a changed file asks for approval again.

    uv run python tools/gui_flows/v2-43-seed.py <folder>
    uv run python tools/gui_flows/v2-43-seed.py <folder> --edit
"""

from __future__ import annotations

import argparse
import time
import importlib.util
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _copy_frames():
    """``copy_frames`` of the #39 seed (its file name is not an importable module name)."""
    spec = importlib.util.spec_from_file_location("v2_39_seed", HERE / "v2-39-seed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.copy_frames


LINK_FILE = Path(".specto") / "links" / "cam_a_to_cam_b.py"

SAME_FRAME = '''\
"""Picking a cam-a frame picks the cam-b frame with the same number."""


def resolve(items, params, root):
    return {it["key"]: it["key"].replace("cam-a/", "cam-b/") for it in items}
'''

NEXT_FRAME = '''\
"""Picking a cam-a frame picks the NEXT cam-b frame (edited after the first approval)."""


def resolve(items, params, root):
    out = {}
    for it in items:
        frame = int(it["key"].rsplit("/", 1)[1])
        out[it["key"]] = f"cam-b/{frame + 1:05d}"
    return out
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--edit", action="store_true", help="rewrite the link file (next frame)")
    ns = ap.parse_args()
    root = ns.folder
    target = root / LINK_FILE
    # a nonce makes the file's hash new every run: approval is per exact content and the
    # trust list outlives the project folder (ADR 0006), so a repeated flow must ask again
    nonce = "\n# seeded " + str(time.time_ns()) + "\n"
    if ns.edit:
        target.write_text(NEXT_FRAME + nonce, encoding="utf-8")
        print(f"[OK] edited {target}")
        return 0
    copy_frames = _copy_frames()
    if root.exists():
        shutil.rmtree(root)
    front = copy_frames(root / "runs" / "run-001" / "cam-a", 12, "cam-a")
    rear = copy_frames(root / "runs" / "run-001" / "cam-b", 8, "cam-b")
    (root / "notes").mkdir(parents=True, exist_ok=True)
    (root / "notes" / "readme.md").write_text("# run-001 review\n", encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SAME_FRAME + nonce, encoding="utf-8")
    print(f"[OK] seeded {root}: {len(front)} cam-a, {len(rear)} cam-b frames, {LINK_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
