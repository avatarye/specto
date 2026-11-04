"""The usd view's collision view (#153): the stage's colliders as one overlay layer both
renderers draw unlit, with the rest of the geometry hidden.

A collider is a prim with UsdPhysics ``CollisionAPI``. Its geometry is what the physics
engine would use, where it can be drawn:

- a Mesh by its ``MeshCollisionAPI`` approximation: ``none``, ``meshSimplification`` and
  ``sdf`` draw the mesh itself, ``convexHull`` its convex hull, ``boundingCube`` its
  local box, ``boundingSphere`` the sphere around its local box's centre;
  ``convexDecomposition`` (no decomposition here) draws as its convex hull, and the note
  says so;
- Cube, Sphere, Capsule, Cylinder, Cone and Plane: their own shape, tessellated;
- anything else (Points, curves) cannot be drawn and is counted in the note.

Every collider becomes one triangle Mesh in world space, at the time asked for, in a flat
tint whose faces are shaded by a fixed light, baked into a per-face ``displayColor``: the
view needs no lights (RTX shows it through its albedo debug view, Storm with lighting
off). :func:`build_overlay` returns the layer (``defaultPrim`` ``Collision``, referenced
at :data:`ROOT`), the stage's gprims to hide with their own visibility (to put back), and
which overlay prim draws which collider (a pick maps back).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt

ROOT = "/__specto/Collision"
#: the tints, one per collider in turn
TINTS = (
    (0.20, 0.80, 0.45),
    (0.25, 0.65, 0.95),
    (0.95, 0.70, 0.20),
    (0.85, 0.40, 0.85),
    (0.30, 0.85, 0.85),
    (0.90, 0.45, 0.35),
)
SEGMENTS = 24  # around a round shape
RINGS = 12  # pole to pole on a sphere
AMBIENT = 0.35  # a face turned away from the light keeps this much of its tint
MESH_ITSELF = ("none", "meshSimplification", "sdf")
SHAPES = ("Cube", "Sphere", "Capsule", "Cylinder", "Cone", "Plane")


@dataclass
class CollisionOverlay:
    layer: Sdf.Layer
    #: the stage's gprims (and instances) to hide, each with its own visibility
    hidden: dict[str, str]
    #: overlay prim path (under ``ROOT``) -> the collider it draws
    sources: dict[str, str]
    colliders: int
    note: str
    stage: Any = field(default=None, repr=False)  # the in-memory stage that owns the layer

    def usda(self) -> str:
        return self.layer.ExportToString()

    def source_of(self, path: str) -> str | None:
        """The collider an overlay prim (or a prim below it) draws, else None."""
        while path.startswith(ROOT + "/"):
            if path in self.sources:
                return self.sources[path]
            path = path.rsplit("/", 1)[0]
        return None


# -- geometry ---------------------------------------------------------------------------
def fan(counts, indices) -> np.ndarray:
    """A mesh's faces as triangles (fans), (T, 3) indices."""
    counts = np.asarray(counts, np.int64)
    indices = np.asarray(indices, np.int64)
    tris = []
    start = 0
    for n in counts:
        if n >= 3:
            face = indices[start : start + n]
            tris.append(np.stack([np.full(n - 2, face[0]), face[1:-1], face[2:]], axis=1))
        start += n
    return np.concatenate(tris) if tris else np.zeros((0, 3), np.int64)


def convex_hull(points) -> tuple[np.ndarray, np.ndarray] | None:
    """The convex hull of ``points`` (N, 3) as (vertices, outward triangles), or None when
    they are flat or fewer than four. Qhull (SPEC-33: a dense collider's hull in
    milliseconds, where pure Python took minutes); its facets come triangulated, each
    turned to face out along the plane Qhull gives it."""
    from scipy.spatial import ConvexHull, QhullError

    pts = np.unique(np.asarray(points, np.float64).reshape(-1, 3), axis=0)
    if len(pts) < 4:
        return None
    try:
        hull = ConvexHull(pts)
    except (QhullError, ValueError):
        return None  # flat (or worse): no volume to draw
    tris = hull.simplices.astype(np.int64)
    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    inward = np.einsum("fk,fk->f", np.cross(b - a, c - a), hull.equations[:, :3]) < 0
    tris[inward] = tris[inward][:, [0, 2, 1]]
    used, tris = np.unique(tris, return_inverse=True)
    return pts[used], tris.reshape(-1, 3)


def box(lo, hi) -> tuple[np.ndarray, np.ndarray]:
    """An axis-aligned box from ``lo`` to ``hi``: 8 vertices, 12 outward triangles."""
    lo, hi = np.asarray(lo, np.float64), np.asarray(hi, np.float64)
    v = np.array([[(hi if (i >> k) & 1 else lo)[k] for k in range(3)] for i in range(8)])
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    tris = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
    return v, np.array(tris, np.int64)


def lathe(profile, axis: str = "Z", segments: int = SEGMENTS) -> tuple[np.ndarray, np.ndarray]:
    """A surface of revolution: ``profile`` is ``(radius, height)`` from one pole to the
    other, turned about ``axis`` through the origin."""
    ang = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    rings = [
        np.stack([r * np.cos(ang), r * np.sin(ang), np.full(segments, h)], 1) for r, h in profile
    ]
    v = np.concatenate(rings)
    tris = []
    for i in range(len(profile) - 1):
        a, b = i * segments, (i + 1) * segments
        for j in range(segments):
            k = (j + 1) % segments
            tris += [(a + j, a + k, b + k), (a + j, b + k, b + j)]
    order = {"X": [2, 0, 1], "Y": [1, 2, 0], "Z": [0, 1, 2]}[axis]
    return v[:, order], np.array(tris, np.int64)


def sphere(center, radius: float, axis: str = "Z") -> tuple[np.ndarray, np.ndarray]:
    t = np.linspace(-np.pi / 2, np.pi / 2, RINGS + 1)
    v, tris = lathe(list(zip(radius * np.cos(t), radius * np.sin(t), strict=True)), axis)
    return v + np.asarray(center, np.float64), tris


def capsule(radius: float, height: float, axis: str) -> tuple[np.ndarray, np.ndarray]:
    t = np.linspace(-np.pi / 2, 0, RINGS // 2 + 1)
    low = [(radius * np.cos(a), -height / 2 + radius * np.sin(a)) for a in t]
    high = [(r, -h) for r, h in reversed(low)]
    return lathe(low + high, axis)


def shape(prim: Usd.Prim, t: Usd.TimeCode) -> tuple[np.ndarray, np.ndarray] | None:
    """An implicit gprim's own shape in its local space."""
    kind = prim.GetTypeName()

    def get(name: str, fallback: Any) -> Any:
        attr = prim.GetAttribute(name)
        value = attr.Get(t) if attr else None
        return fallback if value is None else value

    axis = str(get("axis", "Z"))
    if kind == "Cube":
        s = float(get("size", 2.0)) / 2
        return box((-s, -s, -s), (s, s, s))
    if kind == "Sphere":
        return sphere((0, 0, 0), float(get("radius", 1.0)))
    if kind == "Capsule":
        return capsule(float(get("radius", 0.5)), float(get("height", 1.0)), axis)
    if kind == "Cylinder":
        r, h = float(get("radius", 1.0)), float(get("height", 2.0))
        return lathe([(0, -h / 2), (r, -h / 2), (r, h / 2), (0, h / 2)], axis)
    if kind == "Cone":
        r, h = float(get("radius", 1.0)), float(get("height", 2.0))
        return lathe([(0, -h / 2), (r, -h / 2), (0, h / 2)], axis)
    if kind == "Plane":
        w, ln = float(get("width", 2.0)) / 2, float(get("length", 2.0)) / 2
        v = {"X": [(0, -w, -ln), (0, w, -ln), (0, w, ln), (0, -w, ln)],
             "Y": [(-w, 0, -ln), (-w, 0, ln), (w, 0, ln), (w, 0, -ln)],
             "Z": [(-w, -ln, 0), (w, -ln, 0), (w, ln, 0), (-w, ln, 0)]}[axis]  # fmt: skip
        return np.array(v, np.float64), np.array([(0, 1, 2), (0, 2, 3)], np.int64)
    return None


def collider_geometry(
    prim: Usd.Prim, t: Usd.TimeCode
) -> tuple[tuple[np.ndarray, np.ndarray] | None, str]:
    """A collider's geometry in its local space and how it was drawn (the approximation,
    or the shape's type); None when it cannot be drawn."""
    if prim.GetTypeName() in SHAPES:
        return shape(prim, t), str(prim.GetTypeName())
    if not prim.IsA(UsdGeom.Mesh):
        return None, str(prim.GetTypeName())
    mesh = UsdGeom.Mesh(prim)
    points = mesh.GetPointsAttr().Get(t)
    if not points:
        return None, "empty mesh"
    pts = np.asarray(points, np.float64).reshape(-1, 3)
    approx = "none"
    if prim.HasAPI(UsdPhysics.MeshCollisionAPI):
        approx = str(UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get() or "none")
    if approx in ("convexHull", "convexDecomposition"):
        return convex_hull(pts) or _mesh(mesh, pts, t), approx
    if approx == "boundingCube":
        return box(pts.min(0), pts.max(0)), approx
    if approx == "boundingSphere":
        c = (pts.min(0) + pts.max(0)) / 2
        return sphere(c, float(np.linalg.norm(pts - c, axis=1).max())), approx
    return _mesh(mesh, pts, t), approx


def _mesh(mesh: UsdGeom.Mesh, pts: np.ndarray, t: Usd.TimeCode) -> tuple[np.ndarray, np.ndarray]:
    counts = mesh.GetFaceVertexCountsAttr().Get(t) or []
    indices = mesh.GetFaceVertexIndicesAttr().Get(t) or []
    return pts, fan(counts, indices)


# -- the overlay ------------------------------------------------------------------------
def light_dir(up_axis: str) -> np.ndarray:
    """The fixed light the faces are shaded by: from above, a little to one side."""
    up = np.array([0.0, 1.0, 0.0] if up_axis == "Y" else [0.0, 0.0, 1.0])
    side = np.array([1.0, 0.0, 0.0])
    other = np.cross(up, side)
    d = 0.85 * up + 0.4 * side + 0.3 * other
    return d / np.linalg.norm(d)


def face_colors(pts: np.ndarray, tris: np.ndarray, tint, light: np.ndarray) -> np.ndarray:
    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    n = np.cross(b - a, c - a)
    length = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, length, out=np.zeros_like(n), where=length > 0)
    lit = AMBIENT + (1 - AMBIENT) * (0.5 + 0.5 * (n @ light))
    return (lit[:, None] * np.asarray(tint, np.float64)[None, :]).astype(np.float32)


def colliders(stage: Usd.Stage) -> list[Usd.Prim]:
    """The prims with ``CollisionAPI`` (inside instances too), in stage order."""
    return [
        p
        for p in Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies())
        if p.HasAPI(UsdPhysics.CollisionAPI)
    ]


def hide_list(stage: Usd.Stage, t: Usd.TimeCode) -> dict[str, str]:
    """Every gprim and instance the stage draws, with its own visibility."""
    out: dict[str, str] = {}
    for p in stage.Traverse():
        if p.IsA(UsdGeom.Gprim) or (p.IsInstance() and p.IsA(UsdGeom.Imageable)):
            vis = UsdGeom.Imageable(p).GetVisibilityAttr().Get(t)
            out[str(p.GetPath())] = str(vis or "inherited")
    return out


def build_overlay(
    stage: Usd.Stage, up_axis: str = "Y", t: Usd.TimeCode | None = None
) -> CollisionOverlay:
    t = Usd.TimeCode.Default() if t is None else t
    light = light_dir(up_axis)
    xforms = UsdGeom.XformCache(t)
    keep = Usd.Stage.CreateInMemory("specto-collision.usda")
    UsdGeom.Scope.Define(keep, "/Collision")
    keep.SetDefaultPrim(keep.GetPrimAtPath("/Collision"))
    sources: dict[str, str] = {}
    skipped: list[str] = []
    hulls = 0
    found = colliders(stage)
    for prim in found:
        geometry, how = collider_geometry(prim, t)
        if geometry is None or not len(geometry[1]):
            skipped.append(how)
            continue
        hulls += how == "convexDecomposition"
        local, tris = geometry
        m = np.array(xforms.GetLocalToWorldTransform(prim), np.float64)
        world = np.hstack([local, np.ones((len(local), 1))]) @ m
        pts = world[:, :3] / world[:, 3:4]
        name = f"C{len(sources):05d}"
        mesh = UsdGeom.Mesh.Define(keep, f"/Collision/{name}")
        mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(pts.astype(np.float32)))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(tris), 3, np.int32)))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(tris.astype(np.int32).ravel()))
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        mesh.CreateDoubleSidedAttr(True)
        mesh.CreateExtentAttr(
            Vt.Vec3fArray([Gf.Vec3f(*pts.min(0).tolist()), Gf.Vec3f(*pts.max(0).tolist())])
        )
        color = face_colors(pts, tris, TINTS[len(sources) % len(TINTS)], light)
        mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.uniform).Set(Vt.Vec3fArray.FromNumpy(color))
        sources[f"{ROOT}/{name}"] = str(prim.GetPath())
    words = [f"{len(sources)} collider{'s' if len(sources) != 1 else ''}"]
    if hulls:
        words.append(f"{hulls} convexDecomposition drawn as convex hull")
    if skipped:
        words.append(f"{len(skipped)} not drawable ({', '.join(sorted(set(skipped)))})")
    return CollisionOverlay(
        layer=keep.GetRootLayer(),
        hidden=hide_list(stage, t),
        sources=sources,
        colliders=len(found),
        note="; ".join(words),
        stage=keep,
    )


__all__ = ["ROOT", "CollisionOverlay", "build_overlay", "colliders", "convex_hull", "fan"]
