"""What ``snap`` reports of a usd view (#142): the stage shown, the renderer running, the
frame size, the camera and the environment light (SPEC-31). Qt-free; the usd view's host
(#137, #139) merges it into its ``snapshot``, filled from its params, the machine's
capabilities and what its source reported. Stage facts the source reads (up axis,
bounds, prim count) and selection and time stay the host's own keys.

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
    #: the environment light (SPEC-31): what shows (the source's answer, ``params`` before
    #: it says), why it is not the choice, the sky and HDRI named, the sky's controls in
    #: effect (hours; 0-1), its ground, and the fetch of the sky and the HDRI
    #: (``{cached, fetching, queued, done, total, error}``)
    environment: Literal["stage", "sky", "hdri"] = "stage"
    environment_note: str = ""
    sky: str = "ClearSky"
    hdri: str = "sunny"
    sky_controls: dict[str, float] = {}
    ground: bool = False
    sky_fetch: dict[str, Any] = {}
    hdri_fetch: dict[str, Any] = {}
    #: why the stub USD source plays (SPEC-26): what the renderers tried lack, a sentence;
    #: "" when a renderer runs or the stub was asked for
    fallback_reason: str = ""
    #: where the shown stage's load spent its time, in seconds (SPEC-27; the source's
    #: ``load_timings``: renderer_s, open_s, populate_s, environment_s, first_frame_s,
    #: total_s); {} while a load runs, after a failed one, or with no stage
    load_timings: dict[str, float] = {}


def usd_snap_meta(
    params: Mapping[str, Any],
    caps: Mapping[str, MachineCapability],
    *,
    path: str | None = None,
    renderer: str | None = None,
    frame_size: tuple[int, int] | None = None,
    camera: CameraValue | Mapping[str, Any] | None = None,
    environment: Mapping[str, Any] | None = None,
    fallback_reason: str = "",
    load_timings: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """``UsdSnapMeta`` as JSON for a usd view with ``params`` on a machine with ``caps``;
    the keyword arguments are what the host and its source know (None: not yet;
    ``environment``: the host's environment keys, else the params' choice)."""
    running = renderer or usd_renderer(params, caps)
    env = dict(environment or {})
    for key in ("environment", "sky", "hdri", "ground"):
        if key not in env and params.get(key) is not None:
            env[key] = params[key]
    meta = UsdSnapMeta(
        path=path,
        renderer=running,  # type: ignore[arg-type]
        renderer_asked=params.get("renderer") or "auto",
        mode=(params.get("mode") or "rt") if running == "rtx" else None,
        frame_size=frame_size,
        camera=CameraValue.model_validate(camera) if camera is not None else None,
        look_through=params.get("look_through"),
        camera_speed=float(params.get("camera_speed", 1.0)),
        fallback_reason=fallback_reason,
        load_timings=dict(load_timings or {}),
        **{k: v for k, v in env.items() if k in UsdSnapMeta.model_fields},
    )
    return meta.model_dump(mode="json")


__all__ = ["UsdSnapMeta", "usd_snap_meta"]
