"""A fake ``ovrtx`` 0.6 for seam-2 tests of ``specto-usd-source`` (#137). Every render
product renders a solid ``FAKE_COLOR`` ``LdrColor`` at the product's current resolution;
calls are recorded as JSON lines in ``$FAKE_OVRTX_LOG`` (see the fake ``ovstage``).

``$FAKE_OVRTX_FAIL_STEPS``: comma-separated step numbers (from 1, counted per process)
that raise the way ovrtx 0.6 did once in the spike (#134).

Picks (#140): a step after ``enqueue_pick_query`` also renders ``PICK_HIT``, the hit
``$FAKE_OVRTX_PICK`` names as ``<prim path>@<x,y,z metres>@<normal>`` (unset or empty:
nothing hit). Selection outlines are only recorded.
"""

from __future__ import annotations

import os
from enum import Enum, IntEnum

import numpy as np
from ovstage import log

__version__ = "0.6.0.fake"
FAKE_COLOR = (40, 80, 120, 255)
OVRTX_PICK_FLAG_INCLUDE_TRACKED_INFO = 1 << 1
OVRTX_RENDER_VAR_PICK_HIT = "ovrtx_pick_hit"
_steps = 0  # every renderer's steps in this process


class Device(Enum):
    CPU = 0
    CUDA = 1


class TextureStreamingMode(IntEnum):
    DISABLE = 0
    SYNCHRONOUS = 1
    ASYNCHRONOUS = 2


class RendererConfig:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class SelectionGroupStyle:
    def __init__(self, outline_color, fill_color) -> None:
        self.outline_color = tuple(outline_color)
        self.fill_color = tuple(fill_color)


class _Mapped:
    def __init__(self, array: np.ndarray) -> None:
        self._array = array

    def __dlpack__(self, *args, **kwargs):
        return self._array.__dlpack__(*args, **kwargs)

    def __dlpack_device__(self):
        return self._array.__dlpack_device__()

    def unmap(self) -> None:
        log("unmap")


class _MappedHits:
    """A mapped ``PICK_HIT``: ``params["hitCount"]`` and one tensor per field."""

    def __init__(self, fields: dict[str, np.ndarray], hits: int) -> None:
        self._fields = fields
        self.params = {"hitCount": np.asarray([hits], np.uint32)}

    def __getitem__(self, name: str) -> np.ndarray:
        return self._fields[name]

    def unmap(self) -> None:
        log("unmap")


class _HitVar:
    def __init__(self, fields: dict[str, np.ndarray], hits: int) -> None:
        self._fields, self._hits = fields, hits

    def map(self, device=Device.CPU) -> _MappedHits:
        return _MappedHits(self._fields, self._hits)


def _pick_hit(paths: dict[int, str]) -> _HitVar:
    spec = os.environ.get("FAKE_OVRTX_PICK", "")
    if not spec:
        fields = {
            "primPath": np.zeros(0, np.uint64),
            "worldPositionM": np.zeros((0, 3), np.float64),
            "worldNormal": np.zeros((0, 3), np.float32),
        }
        return _HitVar(fields, 0)
    path, pos, normal = spec.split("@")
    pid = len(paths) + 1
    paths[pid] = path
    fields = {
        "primPath": np.asarray([pid], np.uint64),
        "worldPositionM": np.asarray([[float(v) for v in pos.split(",")]], np.float64),
        "worldNormal": np.asarray([[float(v) for v in normal.split(",")]], np.float32),
    }
    return _HitVar(fields, 1)


class _Var:
    def __init__(self, array: np.ndarray) -> None:
        self._array = array

    def map(self, device=Device.CPU) -> _Mapped:
        return _Mapped(self._array)


class _Frame:
    def __init__(self, render_vars: dict) -> None:
        self.render_vars = render_vars
        self.progression = 1
        self.converged = False


class _Product:
    def __init__(self, frame: _Frame) -> None:
        self.frames = [frame]


class _Fetch:
    def __init__(self, outputs) -> None:
        self._outputs = outputs

    def fetch(self, *args, **kwargs):
        return self._outputs


class _Op:
    def __init__(self, outputs=None, error: Exception | None = None) -> None:
        self._outputs = outputs
        self._error = error

    def wait(self, *args, **kwargs) -> _Fetch:
        if self._error is not None:
            raise self._error
        return _Fetch(self._outputs)


class Renderer:
    def __init__(self, config: RendererConfig | None = None) -> None:
        self.stage = None
        fail = os.environ.get("FAKE_OVRTX_FAIL_STEPS", "")
        self._fail = {int(s) for s in fail.split(",") if s.strip()}
        self._picks: set[str] = set()  # products with a pick query for the next step
        self._paths: dict[int, str] = {}
        log("Renderer", config=dict(config.kwargs) if config else {})

    def attach_ovstage(self, stage) -> None:
        self.stage = stage
        log("attach_ovstage", name=stage.name)

    def detach_ovstage(self) -> None:
        self.stage = None
        log("detach_ovstage")

    def _render(self, render_products: set[str]) -> dict:
        out = {}
        for rp in render_products:
            ((w, h),) = self.stage.attrs[(rp, "resolution")]
            pixels = np.empty((h, w, 4), np.uint8)
            pixels[:] = FAKE_COLOR
            render_vars = {f"{rp}/LdrColor": _Var(pixels)}
            if rp in self._picks:
                render_vars[OVRTX_RENDER_VAR_PICK_HIT] = _pick_hit(self._paths)
            out[rp] = _Product(_Frame(render_vars))
        self._picks.clear()
        return out

    def enqueue_pick_query(self, render_product_path, left, top, right, bottom, *, flags=0):
        self._picks.add(render_product_path)
        log(
            "enqueue_pick_query",
            product=render_product_path,
            rect=[left, top, right, bottom],
            flags=flags,
        )

    def resolve_prim_path_id(self, prim_path_id: int) -> str:
        return self._paths[int(prim_path_id)]

    def set_selection_group_styles(self, styles: dict) -> None:
        log(
            "set_selection_group_styles",
            styles={
                str(g): {"outline": list(s.outline_color), "fill": list(s.fill_color)}
                for g, s in styles.items()
            },
        )

    def set_selection_outline_group_strings(self, prim_paths, group_ids) -> None:
        log("set_selection_outline_group_strings", paths=list(prim_paths), groups=group_ids)

    def step_async(self, render_products, delta_time, *, ordinal=None) -> _Op:
        global _steps
        _steps += 1
        log("step", n=_steps, ordinal=ordinal, delta_time=delta_time)
        if _steps in self._fail:
            return _Op(error=RuntimeError("Operation 'step(...)' failed: "))
        return _Op(self._render(render_products))

    def step(self, render_products, delta_time, *, ordinal=None):
        return self.step_async(render_products, delta_time, ordinal=ordinal).wait().fetch()

    def reset(self, time: float = 0.0) -> None:
        log("reset", time=time)

    def reset_stage(self) -> None:
        log("reset_stage")

    def destroy(self) -> None:
        log("Renderer.destroy")
