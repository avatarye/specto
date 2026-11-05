"""The collision view's overlay (#153, seam 1): which prims are colliders, what each draws
by its approximation, in world space and a shaded flat tint, and what gets hidden."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

from pxr import Sdf, Usd, UsdGeom

from specto_app.usd.collision import ROOT, BuildCancelled, build_overlay, convex_hull, fan

CUBE = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]

STAGE = """#usda 1.0
(
    defaultPrim = "World"
    upAxis = "Z"
)

def Xform "World"
{
    def Cube "Crate" (
        prepend apiSchemas = ["PhysicsCollisionAPI"]
    )
    {
        double size = 2
        double3 xformOp:translate = (10, 0, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Mesh "Rock" (
        prepend apiSchemas = ["PhysicsCollisionAPI", "PhysicsMeshCollisionAPI"]
    )
    {
        uniform token physics:approximation = "convexHull"
        int[] faceVertexCounts = [3, 3, 3, 3]
        int[] faceVertexIndices = [0, 1, 2, 0, 1, 3, 0, 2, 3, 1, 2, 3]
        point3f[] points = [(0, 0, 0), (2, 0, 0), (0, 2, 0), (0, 0, 2), (0.2, 0.2, 0.2)]
    }

    def Mesh "Hedge" (
        prepend apiSchemas = ["PhysicsCollisionAPI", "PhysicsMeshCollisionAPI"]
    )
    {
        uniform token physics:approximation = "boundingCube"
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(-1, -2, 0), (1, 2, 0), (0, 0, 3)]
        double3 xformOp:translate = (0, 20, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Mesh "Bush" (
        prepend apiSchemas = ["PhysicsCollisionAPI", "PhysicsMeshCollisionAPI"]
    )
    {
        uniform token physics:approximation = "convexDecomposition"
        int[] faceVertexCounts = [3, 3, 3, 3]
        int[] faceVertexIndices = [0, 1, 2, 0, 1, 3, 0, 2, 3, 1, 2, 3]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
    }

    def Points "Dust" (
        prepend apiSchemas = ["PhysicsCollisionAPI"]
    )
    {
        point3f[] points = [(0, 0, 0)]
    }

    def Sphere "Decor"
    {
        token visibility = "invisible"
    }
}
"""


@pytest.fixture
def stage() -> Usd.Stage:
    layer = Sdf.Layer.CreateAnonymous(".usda")
    layer.ImportFromString(STAGE)
    return Usd.Stage.Open(layer)


def test_fan_splits_polygons_into_triangles():
    tris = fan([4, 3], [0, 1, 2, 3, 4, 5, 6])
    assert tris.tolist() == [[0, 1, 2], [0, 2, 3], [4, 5, 6]]


def test_the_hull_of_a_cube_with_points_inside_is_its_eight_corners():
    rng = np.random.default_rng(1)
    inside = rng.uniform(-0.9, 0.9, (200, 3))
    verts, tris = convex_hull(np.vstack([CUBE, inside]))
    assert sorted(map(tuple, verts.tolist())) == sorted(CUBE)
    assert len(tris) == 12
    # outward: every vertex is on or behind every face
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    n = np.cross(b - a, c - a)
    assert (np.einsum("fk,vk->fv", n, verts) - np.einsum("fk,fk->f", n, a)[:, None] <= 1e-9).all()


def test_the_hull_of_points_on_a_sphere_keeps_them_all():
    rng = np.random.default_rng(2)
    p = rng.normal(size=(300, 3))
    p /= np.linalg.norm(p, axis=1, keepdims=True)
    verts, tris = convex_hull(p)
    assert len(verts) == 300
    assert len(tris) == 2 * 300 - 4  # a closed triangulated sphere: F = 2V - 4


def test_the_hull_of_a_dense_scan_takes_well_under_a_second():
    # SPEC-33: a gripper's 31 k-point collider hull held the render thread for minutes
    rng = np.random.default_rng(3)
    p = rng.normal(size=(30000, 3))
    p /= np.linalg.norm(p, axis=1, keepdims=True)  # every point on the hull: the worst case
    box: list = []
    worker = threading.Thread(target=lambda: box.append(convex_hull(p)), daemon=True)
    t0 = time.perf_counter()
    worker.start()
    worker.join(2.0)
    assert box, "no hull in 2 s"
    assert time.perf_counter() - t0 < 1.0
    verts, tris = box[0]
    assert len(verts) == 30000 and len(tris) == 2 * 30000 - 4


def test_flat_or_too_few_points_have_no_hull():
    assert convex_hull([(0, 0, 0), (1, 0, 0), (0, 1, 0)]) is None
    assert convex_hull([(x, y, 0) for x in range(3) for y in range(3)]) is None


def overlay_mesh(overlay, collider: str) -> UsdGeom.Mesh:
    path = next(p for p, s in overlay.sources.items() if s == collider)
    return UsdGeom.Mesh(overlay.stage.GetPrimAtPath(path.replace(ROOT, "/Collision")))


def test_the_overlay_draws_each_collider_by_its_approximation_in_world_space(stage):
    overlay = build_overlay(stage, "Z")
    assert set(overlay.sources.values()) == {
        "/World/Crate", "/World/Rock", "/World/Hedge", "/World/Bush"
    }  # fmt: skip
    assert overlay.colliders == 5
    crate = np.array(overlay_mesh(overlay, "/World/Crate").GetPointsAttr().Get())
    assert crate.min(0).tolist() == [9, -1, -1] and crate.max(0).tolist() == [11, 1, 1]
    # the convex hull drops the point inside the tetrahedron
    rock = overlay_mesh(overlay, "/World/Rock")
    assert len(rock.GetPointsAttr().Get()) == 4
    assert list(rock.GetFaceVertexCountsAttr().Get()) == [3] * 4
    # boundingCube: the local box, moved by the prim's transform
    hedge = np.array(overlay_mesh(overlay, "/World/Hedge").GetPointsAttr().Get())
    assert hedge.min(0).tolist() == [-1, 18, 0] and hedge.max(0).tolist() == [1, 22, 3]
    assert overlay.note == (
        "4 colliders; 1 convexDecomposition drawn as convex hull; 1 not drawable (Points)"
    )


def test_each_face_has_a_shaded_tint_and_no_lights_are_needed(stage):
    overlay = build_overlay(stage, "Z")
    crate = overlay_mesh(overlay, "/World/Crate")
    colors = UsdGeom.PrimvarsAPI(crate).GetPrimvar("displayColor")
    assert colors.GetInterpolation() == "uniform"
    values = np.array(colors.Get())
    assert len(values) == 12  # one per triangle
    top_bottom = len({tuple(np.round(v, 4)) for v in values})
    assert top_bottom > 1  # faces turned differently shade differently
    assert crate.GetDoubleSidedAttr().Get() is True
    assert crate.GetSubdivisionSchemeAttr().Get() == "none"
    assert not any(p.IsA(UsdGeom.Boundable) and "Light" in p.GetTypeName()
                   for p in overlay.stage.Traverse())  # fmt: skip


def test_every_gprim_is_hidden_and_keeps_its_own_visibility_to_go_back_to(stage):
    overlay = build_overlay(stage, "Z")
    assert overlay.hidden == {
        "/World/Crate": "inherited",
        "/World/Rock": "inherited",
        "/World/Hedge": "inherited",
        "/World/Bush": "inherited",
        "/World/Dust": "inherited",
        "/World/Decor": "invisible",
    }


def test_a_pick_on_the_overlay_maps_back_to_its_collider(stage):
    overlay = build_overlay(stage, "Z")
    path = next(p for p, s in overlay.sources.items() if s == "/World/Rock")
    assert overlay.source_of(path) == "/World/Rock"
    assert overlay.source_of("/World/Rock") is None
    assert "def Mesh" in overlay.usda() and 'defaultPrim = "Collision"' in overlay.usda()


def test_a_stage_without_colliders_has_an_empty_overlay():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Sphere.Define(stage, "/Ball")
    overlay = build_overlay(stage)
    assert overlay.colliders == 0 and overlay.sources == {}
    assert overlay.note == "0 colliders"


def test_a_cancelled_build_stops_between_colliders(stage):
    asked: list[int] = []

    def cancelled() -> bool:
        asked.append(1)
        return len(asked) > 2  # two colliders, then a new stage wants the thread

    with pytest.raises(BuildCancelled):
        build_overlay(stage, "Z", cancelled=cancelled)
    assert len(asked) == 3
    assert build_overlay(stage, "Z", cancelled=lambda: False).colliders == 5
