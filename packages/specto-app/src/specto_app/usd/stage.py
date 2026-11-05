"""The source's read-only view of a stage through usd-core (``pxr``): what the host and the
inline root need from the file (#137). Imported only by ``specto-usd-source``; the GUI
process never loads pxr.

- :func:`open_stage`: the stage with every payload loaded, its root layer's ``upAxis``
  and ``metersPerUnit`` (the inline root copies both: without them ovrtx assumes
  centimetres, spike #134), the default prim's world bounds and the prim count;
- :meth:`StageInfo.query_tree`: the outliner's pages, the shape the stub USD source
  (#139) answers;
- selection (#140): :meth:`StageInfo.reveal` (the outliner's way down to a prim),
  :meth:`StageInfo.gprims_under` (what an outline draws),
  :meth:`StageInfo.properties` (the ``content`` port's JSON), :meth:`StageInfo.bounds_of`
  (framing one prim: its whole subtree's world bounds, #39);
- the collision view (#153): :meth:`StageInfo.collider_count` and
  :meth:`StageInfo.collision`, the overlay of :mod:`.collision`, built once a stage;
- lighting (#148): :class:`Lighting`, the stage's UsdLux lights and those Kit authored,
  from which rtx's inline root picks its exposure and a fallback light.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pxr import Sdf, Usd, UsdGeom, UsdLux

from .collision import CollisionOverlay, build_overlay, colliders

TREE_LIMIT = 100
OUTLINE_LIMIT = 20000  # gprims outlined for one selection
ARRAY_MAX, ARRAY_HEAD = 32, 16  # a property array longer than this shows its head only
NO_LIGHTS_NOTE = "no lights in the stage: a default dome light"


@dataclass(frozen=True)
class Lighting:
    """How a stage is lit (#148). ``lights``: its UsdLux lights (any prim with LightAPI,
    mesh lights included); ``kit_lights``: those authored in a layer Kit saved (it
    carries Kit's ``renderSettings``: a Kit world, the worlds' sky), whose intensities
    are on RTX's photometric scale."""

    lights: int = 0
    kit_lights: int = 0

    @property
    def fallback(self) -> bool:
        """No light of its own: the renderer adds a default dome."""
        return self.lights == 0

    @property
    def auto_exposure(self) -> bool:
        """Unless every light is Kit's: plain UsdLux intensities (USD's default 1,
        Hydra's scale) render black or burnt out under RTX's fixed exposure."""
        return self.fallback or self.kit_lights < self.lights

    @property
    def note(self) -> str:
        return NO_LIGHTS_NOTE if self.fallback else ""

    # with an environment light the viewer adds (SPEC-31: the sky or the HDRI, on Kit's scale)
    def auto_exposure_with(self, environment: bool) -> bool:
        """Under an environment a stage without lights keeps the fixed exposure (the
        environment is on Kit's scale); its own plain lights still ask for auto exposure."""
        return self.kit_lights < self.lights if environment else self.auto_exposure

    def note_with(self, environment: bool) -> str:
        """The readout's note: nothing to say about a default dome while an environment
        lights the stage."""
        return "" if environment else self.note


@dataclass
class StageInfo:
    path: str
    stage: Usd.Stage
    up_axis: str
    meters_per_unit: float
    bounds: list[list[float]] | None
    prims: int
    start_time_code: float = 0.0
    end_time_code: float = 0.0
    time_codes_per_second: float = 24.0
    animated: bool = False
    lighting: Lighting = field(default_factory=Lighting)
    _colliders: int | None = field(default=None, repr=False)
    _collision: CollisionOverlay | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def loaded_event(self, renderer: str) -> dict[str, Any]:
        """The ``loaded`` event's params."""
        return {
            "path": self.path,
            "name": self.name,
            "renderer": renderer,
            "up_axis": self.up_axis,
            "meters_per_unit": self.meters_per_unit,
            "bounds": self.bounds,
            "prims": self.prims,
            "start_time_code": self.start_time_code,
            "end_time_code": self.end_time_code,
            "time_codes_per_second": self.time_codes_per_second,
            "animated": self.animated,
        }

    # -- the outliner ------------------------------------------------------------------
    def _node(self, prim: Usd.Prim, depth: int) -> dict[str, Any]:
        children = list(prim.GetChildren())
        out = {
            "path": str(prim.GetPath()),
            "name": prim.GetName(),
            "type": str(prim.GetTypeName()),
            "count": len(children),
        }
        if depth > 0 and children:
            out["children"] = [self._node(c, depth - 1) for c in children]
        return out

    def query_tree(self, params: dict[str, Any]) -> dict[str, Any]:
        """``{path, depth, offset, limit}``: a page of ``path``'s children (``"/"``: the
        root prims), ``depth`` levels deep; ``{filter}``: prims whose name holds the text."""
        limit = max(1, int(params.get("limit", TREE_LIMIT)))
        text = str(params.get("filter") or "").lower()
        if text:
            hits = [p for p in self.stage.Traverse() if text in p.GetName().lower()]
            return {
                "filter": text,
                "matches": [self._node(p, 0) for p in hits[:limit]],
                "total": len(hits),
            }
        path = str(params.get("path") or "/")
        prim = self.stage.GetPrimAtPath(path)
        if not prim:
            raise ValueError(f"no prim {path}")
        depth = max(1, int(params.get("depth", 1)))
        offset = max(0, int(params.get("offset", 0)))
        children = list(prim.GetChildren())
        return {
            "path": path,
            "offset": offset,
            "total": len(children),
            "children": [self._node(c, depth - 1) for c in children[offset : offset + limit]],
        }

    def reveal(self, params: dict[str, Any]) -> dict[str, Any]:
        """``{path, limit}``: the outliner pages that show ``path``, one per ancestor from
        ``"/"`` down, each from the first child and at least ``limit`` long, long enough
        to hold the way down (``query_tree``'s page shape)."""
        prim = self._prim(str(params.get("path") or ""))
        limit = max(1, int(params.get("limit", TREE_LIMIT)))
        chain = [prim]
        while chain[-1].GetParent() and not chain[-1].GetParent().IsPseudoRoot():
            chain.append(chain[-1].GetParent())
        chain.append(self.stage.GetPseudoRoot())
        chain.reverse()
        levels = []
        for parent, child in itertools.pairwise(chain):
            children = list(parent.GetChildren())
            at = next(i for i, c in enumerate(children) if c.GetPath() == child.GetPath())
            levels.append(
                {
                    "path": str(parent.GetPath()),
                    "offset": 0,
                    "total": len(children),
                    "children": [self._node(c, 0) for c in children[: max(limit, at + 1)]],
                }
            )
        return {"path": str(prim.GetPath()), "levels": levels}

    # -- selection (#140) --------------------------------------------------------------
    def gprims_under(self, paths: list[str], limit: int = OUTLINE_LIMIT) -> list[str]:
        """The geometry that draws ``paths``: each prim itself when it is a gprim, else the
        gprims below it, at most ``limit`` in all (an Xform has nothing to outline)."""
        out: list[str] = []
        seen: set[str] = set()
        for path in paths:
            prim = self.stage.GetPrimAtPath(path)
            if not prim:
                continue
            for p in Usd.PrimRange(prim):
                if p.IsA(UsdGeom.Gprim) and str(p.GetPath()) not in seen:
                    seen.add(str(p.GetPath()))
                    out.append(str(p.GetPath()))
                    if len(out) >= limit:
                        return out
        return out

    def _prim(self, path: str) -> Usd.Prim:
        prim = self.stage.GetPrimAtPath(path) if path else None
        if not prim:
            raise ValueError(f"no prim {path}")
        return prim

    def properties(self, path: str) -> dict[str, Any]:
        """The prim at ``path`` as JSON: its type, kind, applied schemas and every
        property's resolved value (the default time, else the first time sample; a
        relationship is its targets; a long array its length and head)."""
        prim = self._prim(path)
        props: dict[str, Any] = {}
        for prop in prim.GetProperties():
            if isinstance(prop, Usd.Relationship):
                props[prop.GetName()] = [str(t) for t in prop.GetTargets()]
                continue
            value = prop.Get()
            if value is None and prop.GetNumTimeSamples():
                value = prop.Get(Usd.TimeCode.EarliestTime())
            props[prop.GetName()] = plain(value)
        return {
            "path": str(prim.GetPath()),
            "name": prim.GetName(),
            "type": str(prim.GetTypeName()),
            "kind": Usd.ModelAPI(prim).GetKind() or None,
            "schemas": [str(s) for s in prim.GetAppliedSchemas()],
            "properties": props,
        }

    # -- the collision view (#153) -----------------------------------------------------
    def collider_count(self) -> int:
        """The prims with UsdPhysics ``CollisionAPI``."""
        if self._colliders is None:
            self._colliders = len(colliders(self.stage))
        return self._colliders

    def collision(
        self, timecode: float | None = None, cancelled: Callable[[], bool] | None = None
    ) -> CollisionOverlay:
        """The collision overlay, at ``timecode`` (None: the default time) the first time
        it is asked for; kept for the stage. ``cancelled``: checked between colliders
        (:func:`.collision.build_overlay`)."""
        if self._collision is None:
            t = Usd.TimeCode.Default() if timecode is None else Usd.TimeCode(timecode)
            self._collision = build_overlay(self.stage, self.up_axis, t, cancelled=cancelled)
            self._colliders = self._collision.colliders
        return self._collision

    def bounds_of(self, path: str, timecode: float | None = None) -> dict[str, Any]:
        """``{path, bounds}``: the world bounds of the prim's whole subtree (#39: an Xform
        or Scope with no geometry of its own is the union of its descendants', as
        ``UsdGeom.BBoxCache`` computes it, the default and render purposes, invisible
        prims left out), at ``timecode`` (None: the default time); None when nothing
        under it draws."""
        prim = self._prim(path)
        box = _bbox_cache(timecode).ComputeWorldBound(prim).ComputeAlignedRange()
        bounds = None if box.IsEmpty() else [list(box.GetMin()), list(box.GetMax())]
        return {"path": str(prim.GetPath()), "bounds": bounds}


def plain(value: Any) -> Any:
    """A USD value as JSON: Gf vectors and matrices as lists, asset paths resolved, a
    quaternion as ``[real, i, j, k]``, an array longer than ``ARRAY_MAX`` as ``{length,
    head}``; anything unknown as its string."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Sdf.AssetPath):
        return value.resolvedPath or value.path
    if isinstance(value, Sdf.Path):
        return str(value)
    module = type(value).__module__
    if module.startswith("pxr.Gf") and "Quat" in type(value).__name__:
        return [float(value.GetReal()), *(float(v) for v in value.GetImaginary())]
    if module.startswith("pxr.Vt") or isinstance(value, list | tuple):
        if len(value) > ARRAY_MAX:
            return {"length": len(value), "head": [plain(v) for v in list(value[:ARRAY_HEAD])]}
        return [plain(v) for v in value]
    if module.startswith("pxr.Gf"):
        try:
            return [plain(v) for v in value]
        except TypeError:
            return str(value)
    return str(value)


def _bbox_cache(timecode: float | None = None) -> UsdGeom.BBoxCache:
    """A fresh bound cache at ``timecode`` (None: the default time) over the default and
    render purposes, honouring visibility (a hierarchical bound: a prim's is its
    subtree's)."""
    t = Usd.TimeCode.Default() if timecode is None else Usd.TimeCode(timecode)
    return UsdGeom.BBoxCache(t, [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])


def world_bounds(stage: Usd.Stage) -> list[list[float]] | None:
    """The default prim's (else the whole stage's) world bounds, or None when empty."""
    root = stage.GetDefaultPrim() or stage.GetPseudoRoot()
    box = _bbox_cache().ComputeWorldBound(root).ComputeAlignedRange()
    if box.IsEmpty():
        return None
    return [list(box.GetMin()), list(box.GetMax())]


def time_codes(stage: Usd.Stage) -> dict[str, Any]:
    """The stage's time codes (#141): the root layer's range and timeCodesPerSecond
    (USD's fallbacks where it authors none), and ``animated`` when a layer it uses holds
    time samples. An animated stage without an authored range plays over its samples'."""
    start, end = float(stage.GetStartTimeCode()), float(stage.GetEndTimeCode())
    samples: list[float] = []
    for layer in stage.GetUsedLayers():
        times = layer.ListAllTimeSamples()
        if times:
            samples += [min(times), max(times)]
    if samples and end <= start:
        start, end = min(samples), max(samples)
    return {
        "start_time_code": start,
        "end_time_code": end,
        "time_codes_per_second": float(stage.GetTimeCodesPerSecond()),
        "animated": bool(samples) and end > start,
    }


def open_stage(path: str) -> StageInfo:
    stage = Usd.Stage.Open(path, Usd.Stage.LoadAll)
    if stage is None:
        raise ValueError(f"cannot open {path}")
    return StageInfo(
        path=path,
        stage=stage,
        up_axis=str(UsdGeom.GetStageUpAxis(stage)),
        meters_per_unit=float(UsdGeom.GetStageMetersPerUnit(stage)),
        bounds=world_bounds(stage),
        **_prims_and_lighting(stage),
        **time_codes(stage),
    )


def _prims_and_lighting(stage: Usd.Stage) -> dict[str, Any]:
    """The prim count and the :class:`Lighting`, in one traversal. A light is Kit's when
    a layer Kit saved holds one of its specs."""
    kit_layers: dict[str, bool] = {}

    def kit(layer: Sdf.Layer) -> bool:
        if layer.identifier not in kit_layers:
            kit_layers[layer.identifier] = "renderSettings" in layer.customLayerData
        return kit_layers[layer.identifier]

    prims = lights = kit_lights = 0
    for prim in stage.Traverse():
        prims += 1
        if prim.HasAPI(UsdLux.LightAPI):
            lights += 1
            kit_lights += any(kit(spec.layer) for spec in prim.GetPrimStack())
    return {"prims": prims, "lighting": Lighting(lights=lights, kit_lights=kit_lights)}
