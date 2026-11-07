"""Where one stage load's seconds went (SPEC-27): the source measures it
(``specto_app.usd.source``), sends it as ``load_timings`` once the frame its ``ok`` names
is in the ring, and the usd view's host keeps it for ``snap`` (``usd_meta.UsdSnapMeta``).
Qt-free and import-light, so the host side and tools can share the keys."""

from __future__ import annotations

from typing import TypedDict


class LoadTimings(TypedDict, total=False):
    """The phases of one load, in seconds; ``renderer_s`` only for a load that swapped
    in a fresh renderer. The order is the order of the phases."""

    renderer_s: float  # a fresh renderer's swap (``--fresh-renderer``)
    open_s: float  # usd-core opens the stage
    populate_s: float  # the renderer's stage filled
    environment_s: float  # the environment light set up
    first_frame_s: float  # from there to the frame ``ok`` names
    total_s: float
