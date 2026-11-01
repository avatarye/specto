"""A compact kd-tree over a point cloud for picking (spec #35: per-point picking via a
kd-tree built at resolve time; research #31).

The tree is implicit: a permutation of the point indices in which every node is the
median of its segment (``perm[(lo + hi) // 2]``), split on the axis of the segment's
widest extent, with leaves of ``leaf`` points. Nothing else is stored, so the tree is
one ``uint32`` per point plus one ``uint8`` split axis, built with numpy partitions and
saved next to the decoded buffers. Queries run in Python over the implicit structure:
``nearest`` for a world point, ``pick_ray`` for a pick cone from the camera.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

MAGIC = b"SKD1"
HEADER = struct.Struct("<4sIII")  # magic, count, leaf, reserved
DEFAULT_LEAF = 64


class KdTree:
    def __init__(self, points: np.ndarray, perm: np.ndarray, axes: np.ndarray, leaf: int) -> None:
        self.points = points
        self.perm = perm
        self.axes = axes
        self.leaf = leaf
        self.count = len(perm)
        if self.count:
            self.bounds = (
                points.min(axis=0).astype(np.float64),
                points.max(axis=0).astype(np.float64),
            )
        else:
            self.bounds = (np.zeros(3), np.zeros(3))

    # -- build / save / load -----------------------------------------------------------------
    @classmethod
    def build(cls, points: np.ndarray, *, leaf: int = DEFAULT_LEAF) -> KdTree:
        pts = np.ascontiguousarray(points, dtype=np.float32).reshape(-1, 3)
        n = len(pts)
        perm = np.arange(n, dtype=np.uint32)
        axes = np.zeros(n, dtype=np.uint8)
        stack = [(0, n)]
        while stack:
            lo, hi = stack.pop()
            if hi - lo <= leaf:
                continue
            seg = perm[lo:hi]
            sub = pts[seg]
            axis = int((sub.max(axis=0) - sub.min(axis=0)).argmax())
            mid = (lo + hi) // 2
            order = np.argpartition(sub[:, axis], mid - lo)
            perm[lo:hi] = seg[order]
            axes[mid] = axis
            stack.append((lo, mid))
            stack.append((mid + 1, hi))
        return cls(pts, perm, axes, leaf)

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        with path.open("wb") as fh:
            fh.write(HEADER.pack(MAGIC, self.count, self.leaf, 0))
            fh.write(self.perm.astype("<u4").tobytes())
            fh.write(self.axes.tobytes())
        return path

    @classmethod
    def load(cls, path: Path | str, points: np.ndarray) -> KdTree:
        """The saved tree over ``points`` (the same float32 xyz it was built from)."""
        data = Path(path).read_bytes()
        magic, count, leaf, _ = HEADER.unpack_from(data, 0)
        if magic != MAGIC:
            raise ValueError(f"{path}: not a Specto kd-tree file")
        pts = np.ascontiguousarray(points, dtype=np.float32).reshape(-1, 3)
        if len(pts) != count:
            raise ValueError(f"{path}: tree has {count} points, buffer has {len(pts)}")
        off = HEADER.size
        perm = np.frombuffer(data, dtype="<u4", count=count, offset=off).astype(np.uint32)
        axes = np.frombuffer(data, dtype=np.uint8, count=count, offset=off + 4 * count).copy()
        return cls(pts, perm, axes, leaf)

    # -- queries ---------------------------------------------------------------------------------
    def nearest(self, point: np.ndarray | list[float]) -> tuple[int, float]:
        """``(index, distance)`` of the point nearest to ``point``; ``(-1, inf)`` when empty."""
        q = np.asarray(point, dtype=np.float64).reshape(3)
        pts, perm, axes = self.points, self.perm, self.axes
        best_d2, best_i = np.inf, -1
        stack: list[tuple[int, int, float]] = [(0, self.count, 0.0)]
        while stack:
            lo, hi, plane_d2 = stack.pop()
            if plane_d2 >= best_d2 or lo >= hi:
                continue
            if hi - lo <= self.leaf:
                seg = perm[lo:hi]
                d2 = ((pts[seg].astype(np.float64) - q) ** 2).sum(axis=1)
                i = int(d2.argmin())
                if d2[i] < best_d2:
                    best_d2, best_i = float(d2[i]), int(seg[i])
                continue
            mid = (lo + hi) // 2
            axis = int(axes[mid])
            p = pts[perm[mid]].astype(np.float64)
            d2 = float(((p - q) ** 2).sum())
            if d2 < best_d2:
                best_d2, best_i = d2, int(perm[mid])
            diff = q[axis] - p[axis]
            near, far = ((lo, mid), (mid + 1, hi)) if diff < 0 else ((mid + 1, hi), (lo, mid))
            stack.append((far[0], far[1], diff * diff))
            stack.append((near[0], near[1], 0.0))
        return best_i, float(np.sqrt(best_d2)) if best_i >= 0 else np.inf

    def pick_ray(
        self,
        origin: np.ndarray | list[float],
        direction: np.ndarray | list[float],
        *,
        tolerance: float,
        t_max: float = np.inf,
    ) -> tuple[int, float, float] | None:
        """The point a pick ray hits: the nearest one along the ray inside a cone of half
        angle ``atan(tolerance)`` (``tolerance`` is pixels of slack converted to a
        tangent by the caller). Returns ``(index, t, perpendicular distance)`` or None."""
        o = np.asarray(origin, dtype=np.float64).reshape(3)
        d = np.asarray(direction, dtype=np.float64).reshape(3)
        norm = float(np.linalg.norm(d))
        if norm == 0.0 or self.count == 0:
            return None
        d = d / norm
        inv = np.where(d != 0.0, 1.0 / np.where(d != 0.0, d, 1.0), np.inf)
        pts, perm, axes = self.points, self.perm, self.axes
        tol = float(tolerance)
        best: tuple[int, float, float] | None = None
        limit = float(t_max)

        def slab(bmin: np.ndarray, bmax: np.ndarray, r: float) -> tuple[float, float] | None:
            lo_t = (bmin - r - o) * inv
            hi_t = (bmax + r - o) * inv
            with np.errstate(invalid="ignore"):
                t0 = float(np.nanmax(np.minimum(lo_t, hi_t)))
                t1 = float(np.nanmin(np.maximum(lo_t, hi_t)))
            # an axis with d == 0: inside the slab or out of it altogether
            still = d == 0.0
            if still.any() and (
                (o[still] < bmin[still] - r).any() or (o[still] > bmax[still] + r).any()
            ):
                return None
            t0 = max(t0, 0.0)
            return (t0, t1) if t1 >= t0 else None

        def consider(idx: np.ndarray) -> None:
            nonlocal best, limit
            v = pts[idx].astype(np.float64) - o
            t = v @ d
            perp2 = np.maximum((v * v).sum(axis=1) - t * t, 0.0)
            ok = (t > 0.0) & (t < limit) & (perp2 <= (tol * t) ** 2)
            if not ok.any():
                return
            cand = np.flatnonzero(ok)
            k = cand[int(t[cand].argmin())]
            best = (int(idx[k]), float(t[k]), float(np.sqrt(perp2[k])))
            limit = float(t[k])

        stack: list[tuple[int, int, np.ndarray, np.ndarray]] = [
            (0, self.count, self.bounds[0].copy(), self.bounds[1].copy())
        ]
        while stack:
            lo, hi, bmin, bmax = stack.pop()
            if lo >= hi:
                continue
            hit = slab(bmin, bmax, tol * limit if np.isfinite(limit) else tol * 1e9)
            if hit is None or hit[0] > limit:
                continue
            # tighten: the cone is only as wide as the ray is long inside this box
            r = tol * min(hit[1], limit)
            hit = slab(bmin, bmax, r)
            if hit is None or hit[0] > limit:
                continue
            if hi - lo <= self.leaf:
                consider(perm[lo:hi])
                continue
            mid = (lo + hi) // 2
            axis = int(axes[mid])
            node = perm[mid]
            consider(perm[mid : mid + 1])
            split = float(pts[node][axis])
            lmax = bmax.copy()
            lmax[axis] = split
            rmin = bmin.copy()
            rmin[axis] = split
            left = (lo, mid, bmin, lmax)
            right = (mid + 1, hi, rmin, bmax)
            # the child on the origin's side first (it holds the nearer hits)
            near, far = (left, right) if o[axis] < split else (right, left)
            stack.append(far)
            stack.append(near)
        return best


__all__ = ["DEFAULT_LEAF", "KdTree"]
