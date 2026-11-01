"""Seed the project folder for the v2 #141 scripted flow (the usd view's time): an
animated stage authored here, worlds/drive.usda (a red box crossing a grey ground and a
blue ball rising, 168 timecodes at 24 per second: 7 s), and the cam-a video of run-001
without its sidecar (its own ``video`` clock, seconds from 0, about 7.4 s) under
runs/video/. The stage is lit by a dome-light sky layer from the user's depot
(``SPECTO_P4_SKY``, see ``_depot.py``; referenced read-only, beside the default prim so
the first look frames the drive, not the sky).
The folder is wiped first.

    uv run python tools/gui_flows/v2-141-seed.py <folder>
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path

from _depot import depot_sky

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("v2_39_seed", HERE / "v2-39-seed.py")
_seed = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_seed)
TESTDATA = _seed.TESTDATA

SKY = depot_sky()
STAGE = """#usda 1.0
(
    defaultPrim = "World"
    endTimeCode = 168
    metersPerUnit = 1
    startTimeCode = 0
    timeCodesPerSecond = 24
    upAxis = "Z"
)

def Xform "Sky" (
    prepend references = @SKY@
)
{
}

def Xform "World"
{
    def Mesh "Ground"
    {
        int[] faceVertexCounts = [4]
        int[] faceVertexIndices = [0, 1, 2, 3]
        normal3f[] normals = [(0, 0, 1), (0, 0, 1), (0, 0, 1), (0, 0, 1)]
        point3f[] points = [(-16, -8, 0), (16, -8, 0), (16, 8, 0), (-16, 8, 0)]
        color3f[] primvars:displayColor = [(0.55, 0.55, 0.55)]
    }

    def Cube "Box"
    {
        double size = 2
        color3f[] primvars:displayColor = [(0.9, 0.1, 0.08)]
        double3 xformOp:translate.timeSamples = {
            0: (-12, -2, 1),
            168: (12, -2, 1),
        }
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Sphere "Ball"
    {
        double radius = 1
        color3f[] primvars:displayColor = [(0.1, 0.25, 0.9)]
        double3 xformOp:translate.timeSamples = {
            0: (0, 3, 1),
            84: (0, 3, 6),
            168: (0, 3, 1),
        }
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    root = ap.parse_args().folder
    video = TESTDATA / "runs" / "run-001" / "video" / "cam-a.mp4"
    for need in (SKY, video):
        if not need.is_file():
            print(f"[ERROR] not on this machine: {need}")
            return 1
    if root.exists():
        shutil.rmtree(root)
    (root / "worlds").mkdir(parents=True)
    (root / "worlds" / "drive.usda").write_text(
        STAGE.replace("SKY", SKY.as_posix()), encoding="utf-8"
    )
    (root / "runs" / "video").mkdir(parents=True)
    shutil.copy2(video, root / "runs" / "video" / "cam-a.mp4")
    print(f"[OK] seeded {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
