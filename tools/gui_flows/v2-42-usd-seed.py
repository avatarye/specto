"""Seed the project folder for the SPEC-31 scripted flow (the usd view's environment
light): worlds/plain.usda, a small Z-up metre stage with **no lights**: a grey ground, a
red box, a green ball and a blue wall. Under RTX the Stage choice lights it with the
fallback dome (#148), a Sky with one of NVIDIA's dynamic skies and HDRI with one of the 3d
view's HDRIs (``packages/specto-web/build.py``). The folder is wiped first.

The skies are fetched on first use into the app's user dir, which the GUI flows keep
under ``<project>/.specto/gui-user``: when this machine's own Specto has them cached
already (``specto_app.usd.skies.cache_dir()``), the seed copies that cache there so the
flow runs offline; otherwise the flow's first Sky pick fetches them live from NVIDIA's
bucket (about a second for the procedural skies).

    uv run python tools/gui_flows/v2-42-usd-seed.py <folder>
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

STAGE = """#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)

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
        double3 xformOp:translate = (0, 0, 1)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Sphere "Ball"
    {
        double radius = 1.5
        color3f[] primvars:displayColor = [(0.1, 0.8, 0.2)]
        double3 xformOp:translate = (6, 0, 1.5)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Cube "Wall"
    {
        double size = 3
        color3f[] primvars:displayColor = [(0.1, 0.25, 0.9)]
        double3 xformOp:translate = (-6, 0, 1.5)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
}
"""


def copy_sky_cache(root: Path) -> None:
    """This machine's cached skies into the flow's user dir, when there are any."""
    try:
        from specto_app.usd import skies
    except ImportError:
        return
    source = skies.cache_dir()
    cached = [name for name in skies.SKIES if skies.package(name, source) is not None]
    if not cached:
        print("[INFO] no cached skies on this machine: the flow fetches them live")
        return
    target = root / ".specto" / "gui-user" / "skies"
    shutil.copytree(source, target, dirs_exist_ok=True)
    print(f"[OK] copied the cached skies ({', '.join(cached)}) into {target}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    root = ap.parse_args().folder
    if root.exists():
        shutil.rmtree(root)
    (root / "worlds").mkdir(parents=True)
    (root / "worlds" / "plain.usda").write_text(STAGE, encoding="utf-8", newline="\n")
    copy_sky_cache(root)
    print(f"[OK] seeded {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
