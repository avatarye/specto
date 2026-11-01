"""Seed the project folder for the v2 #153 scripted flows (the usd view's view modes):
worlds/modes.usda, authored here: a grey ground, a red box, a ball with a green
UsdPreviewSurface, and two colliders (UsdPhysics CollisionAPI): a blue wall (a Cube) and
a rock mesh with a convexHull approximation whose hull drops a point inside it. It is
lit by a dome-light sky layer from the user's depot (``SPECTO_P4_SKY``, see
``_depot.py``; referenced read-only, beside the default prim). The folder is wiped first.

    uv run python tools/gui_flows/v2-153-seed.py <folder>
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from _depot import depot_sky

SKY = depot_sky()
STAGE = """#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
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
        double3 xformOp:translate = (0, 0, 1)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Sphere "Ball" (
        prepend apiSchemas = ["MaterialBindingAPI"]
    )
    {
        double radius = 1.5
        rel material:binding = </World/Looks/Green>
        double3 xformOp:translate = (6, 0, 1.5)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Cube "Wall" (
        prepend apiSchemas = ["PhysicsCollisionAPI"]
    )
    {
        double size = 3
        color3f[] primvars:displayColor = [(0.1, 0.25, 0.9)]
        double3 xformOp:translate = (-6, 0, 1.5)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Mesh "Rock" (
        prepend apiSchemas = ["PhysicsCollisionAPI", "PhysicsMeshCollisionAPI"]
    )
    {
        uniform token physics:approximation = "convexHull"
        int[] faceVertexCounts = [3, 3, 3, 3]
        int[] faceVertexIndices = [0, 1, 2, 0, 1, 3, 0, 2, 3, 1, 2, 3]
        point3f[] points = [(-1, -1, 0), (2, -1, 0), (-1, 2, 0), (-1, -1, 3), (0, 0, 0.5)]
        color3f[] primvars:displayColor = [(0.45, 0.4, 0.35)]
        double3 xformOp:translate = (-1, 4, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Scope "Looks"
    {
        def Material "Green"
        {
            token outputs:surface.connect = </World/Looks/Green/Surface.outputs:surface>

            def Shader "Surface"
            {
                uniform token info:id = "UsdPreviewSurface"
                color3f inputs:diffuseColor = (0.1, 0.8, 0.2)
                token outputs:surface
            }
        }
    }
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    root = ap.parse_args().folder
    if not SKY.is_file():
        print(f"[ERROR] not on this machine: {SKY}")
        return 1
    if root.exists():
        shutil.rmtree(root)
    (root / "worlds").mkdir(parents=True)
    (root / "worlds" / "modes.usda").write_text(
        STAGE.replace("SKY", SKY.as_posix()), encoding="utf-8"
    )
    print(f"[OK] seeded {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
