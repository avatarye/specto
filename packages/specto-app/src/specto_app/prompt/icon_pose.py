"""The agent icon's poses (#170): what the S character shows in each icon state, and where
its eyes go for a gaze. No Qt: ``AgentIcon.qml`` reads these from the ``logoArt`` context
property (``art_for_qml``), next to the art itself.

The art (the S, the eyes' rest positions and sizes, how far they may travel) is data, read
from the design files by ``specto_app.logo``; this module holds only the states'
behaviour from the design's README (Figma page 04 "Logo final: S character"): idle blinks,
thinking looks around up high, working scans the bowl end to end, needs you widens the
eyes, error crosses them out. Offsets are in grid units from rest, never past the travel.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..logo import Logo, gaze_offset, logo

GAZE_MS = 250
BLINK_MIN_MS, BLINK_MAX_MS = 4000, 7000
BLINK_CLOSE_MS, BLINK_OPEN_MS = 80, 120
LOOK_HOLD_MS, LOOK_LOOP_MS = 1200, 4000
SCAN_SWEEP_MS = 1200
X_DRAW_MS = 160
#: thinking: the eyes look up and to the right, narrowed
THINK_AT = (5.0, -6.0)
THINK_SQUASH = 0.85
ERROR_OPACITY = 0.45


@dataclass(frozen=True)
class Pose:
    #: open | wide | x
    eyes: str
    #: the eyes' offset from rest, in grid units, where the state's motion starts
    offset: tuple[float, float]
    #: the eyes' height factor (thinking narrows them)
    squash: float
    #: theme colour tokens of the S body, the tile and its border
    body: str
    body_opacity: float
    tile: str
    border: str
    #: blink | look_around | scan | shake | none
    motion: str
    #: whether the eyes follow the gaze (the states without an eye motion of their own)
    gaze: bool


_IDLE = Pose("open", (0, 0), 1.0, "text", 1.0, "surface", "border_strong", "blink", True)


def _clamp(offset: tuple[float, float], travel: tuple[float, float]) -> tuple[float, float]:
    return (
        max(-travel[0], min(travel[0], offset[0])),
        max(-travel[1], min(travel[1], offset[1])),
    )


def poses(art: Logo | None = None) -> dict[str, Pose]:
    """Every state's pose for the art's travel (the app's mark by default)."""
    art = art or logo()
    travel = art.travel
    plain = replace(_IDLE, motion="none", gaze=False)
    return {
        "idle": _IDLE,
        "thinking": replace(
            plain, offset=_clamp(THINK_AT, travel), squash=THINK_SQUASH, motion="look_around"
        ),
        # the scan starts at the left end of the eyes' travel
        "working": replace(plain, offset=(-travel[0], 0), motion="scan"),
        # the wide eyes follow the gaze within their own (smaller) travel, if the art
        # gives them one; else they hold at rest
        "needs_you": Pose(
            "wide", (0, 0), 1.0, "agent", 1.0, "agent_soft", "agent", "shake", any(art.wide_travel)
        ),
        "error": replace(plain, eyes="x", body_opacity=ERROR_OPACITY),
    }


def pose(state: str, art: Logo | None = None) -> Pose:
    """The S in ``state``; a state the art does not know draws as idle."""
    return poses(art).get(state, _IDLE)


def blink_delay(r: float) -> int:
    """The wait before the next idle blink for a uniform ``r`` in [0, 1)."""
    return round(BLINK_MIN_MS + r * (BLINK_MAX_MS - BLINK_MIN_MS))


def art_for_qml(art: Logo | None = None) -> dict[str, Any]:
    """The ``logoArt`` context property: the mark's parts and the states' poses."""
    art = art or logo()
    out = art.to_qml()
    out["poses"] = {
        state: {
            "eyes": p.eyes,
            "dx": p.offset[0],
            "dy": p.offset[1],
            "squash": p.squash,
            "bodyOpacity": p.body_opacity,
            "motion": p.motion,
            "gaze": p.gaze,
        }
        for state, p in poses(art).items()
    }
    return out


__all__ = [
    "ERROR_OPACITY",
    "THINK_AT",
    "THINK_SQUASH",
    "Pose",
    "art_for_qml",
    "blink_delay",
    "gaze_offset",
    "pose",
    "poses",
]
