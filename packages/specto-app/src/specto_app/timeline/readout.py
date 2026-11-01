"""What the video footer reads out (#128): the frame readout at rest, the clip time and
SMPTE timecode in the progress bar's hover tip, and the speed slider's stops.

- ``frame_readout(11, 240, 32.4786)`` -> ``"12 / 240 @ 32.48 fps"``: frames count from 1,
  the rate is the source's own to two places (trailing zeros dropped: ``30 fps``);
- ``clip_time(0.338685)`` -> ``"0:00.34"``: pts from the clip's start, to the hundredth;
- ``timecode(1.046843, 32.4786)`` -> ``"00:00:01:01"``: ``hh:mm:ss:ff``, the seconds of
  the clip time and the frame within that second (so a non-integer rate never drifts
  from the clock: a second holds 32 or 33 frames of a 32.48 fps clip);
- speeds sit on a log2 scale, 0.25x at the slider's foot to 4x at its top, the five
  stops evenly spaced: ``speed_fraction``, ``speed_at`` (snapped to a stop) and
  ``speed_label``.
"""

from __future__ import annotations

import math

SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0)
SPEED_MIN, SPEED_MAX = SPEEDS[0], SPEEDS[-1]
#: frames of slack under a frame boundary: sidecar pts carry six decimals
_EPS = 1e-4


def _num(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def frame_readout(frame: int, count: int, fps: float) -> str:
    if count <= 0 or frame < 0:
        return ""
    s = f"{frame + 1} / {count}"
    return f"{s} @ {_num(fps)} fps" if fps > 0 else s


def clip_time(pts: float) -> str:
    cs = round(max(0.0, pts) * 100)
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, c = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}" if h else f"{m}:{s:02d}.{c:02d}"


def timecode(pts: float, fps: float) -> str:
    pts = max(0.0, pts)
    whole = math.floor(pts + _EPS / max(fps, 1.0))
    ff = max(0, math.floor((pts - whole) * fps + _EPS)) if fps > 0 else 0
    h, rest = divmod(whole, 3600)
    m, s = divmod(rest, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{ff:02d}"


def speed_fraction(speed: float) -> float:
    """Where ``speed`` sits on the slider, 0 (0.25x) .. 1 (4x)."""
    s = max(SPEED_MIN, min(SPEED_MAX, speed))
    return math.log2(s / SPEED_MIN) / math.log2(SPEED_MAX / SPEED_MIN)


def speed_at(fraction: float) -> float:
    """The stop nearest ``fraction`` of the slider."""
    i = round(max(0.0, min(1.0, fraction)) * (len(SPEEDS) - 1))
    return SPEEDS[i]


def speed_label(speed: float) -> str:
    return f"{_num(speed)}x"


def volume_label(volume: float) -> str:
    """The volume slider's tip: a fraction of full as a percentage (``"80 %"``)."""
    return f"{round(max(0.0, min(1.0, volume)) * 100)} %"


__all__ = [
    "SPEEDS",
    "SPEED_MAX",
    "SPEED_MIN",
    "clip_time",
    "frame_readout",
    "speed_at",
    "speed_fraction",
    "speed_label",
    "timecode",
    "volume_label",
]
