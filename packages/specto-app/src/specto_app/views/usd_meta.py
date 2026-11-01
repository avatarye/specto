"""What ``snap`` reports of a usd view (#142): the stage shown, the renderer running, the
frame size and the camera. Qt-free; the usd view's host (#137, #139) merges it into its
``snapshot``, filled from its params, the machine's capabilities and what its source
reported. Stage facts the source reads (up axis, bounds, prim count) and selection and
time stay the host's own keys.

``renderer`` is what runs: the source's word once it reported one (an ``auto`` that
fell back to storm says storm), else ``params.renderer`` resolved against the machine
(``specto_core.machine.usd_renderer``). ``mode`` is the RTX render mode, None under
storm, which has none. ``frame_size`` and ``camera`` are None until the source's first
frame.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel

from specto_core.links import CameraValue
from specto_core.machine import MachineCapability, usd_renderer


class UsdSnapMeta(BaseModel):
    path: str | None
    renderer: Literal["rtx", "storm"]
    renderer_asked: Literal["auto", "rtx", "storm"]
    mode: Literal["rt", "pt", "minimal"] | None
    frame_size: tuple[int, int] | None
    camera: CameraValue | None
    look_through: str | None
    camera_speed: float


def usd_snap_meta(
    params: Mapping[str, Any],
    caps: Mapping[str, MachineCapability],
    *,
    path: str | None = None,
    renderer: str | None = None,
    frame_size: tuple[int, int] | None = None,
    camera: CameraValue | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """``UsdSnapMeta`` as JSON for a usd view with ``params`` on a machine with ``caps``;
    the keyword arguments are what the host and its source know (None: not yet)."""
    running = renderer or usd_renderer(params, caps)
    meta = UsdSnapMeta(
        path=path,
        renderer=running,  # type: ignore[arg-type]
        renderer_asked=params.get("renderer") or "auto",
        mode=(params.get("mode") or "rt") if running == "rtx" else None,
        frame_size=frame_size,
        camera=CameraValue.model_validate(camera) if camera is not None else None,
        look_through=params.get("look_through"),
        camera_speed=float(params.get("camera_speed", 1.0)),
    )
    return meta.model_dump(mode="json")


__all__ = ["UsdSnapMeta", "usd_snap_meta"]
