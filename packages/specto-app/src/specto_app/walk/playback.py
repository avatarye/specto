"""The walk-through's playback state machine (#174), Qt-free: which step of the script is
current and whether auto-advance runs. ``WalkController`` wraps it for QML and owns the
timer.

States: ``idle`` (no walk yet), ``preparing`` (the agent writes the captions; the plan is
shown), ``playing`` (auto-advance on), ``paused``, ``summary`` (the last step; auto-advance
stops there) and ``exited``. A script of N hops has N + 2 steps: 0 the establishing shot,
1..N the hops, N + 1 the summary. Replay reuses the script.
"""

from __future__ import annotations

import re
from typing import Any

IDLE, PREPARING, PLAYING, PAUSED, SUMMARY, EXITED = (
    "idle",
    "preparing",
    "playing",
    "paused",
    "summary",
    "exited",
)
ACTIVE = (PREPARING, PLAYING, PAUSED, SUMMARY)
#: dwell per step (#183, reading speed): 2 s to take the step in, then 300 ms per caption
#: word (about 200 words a minute), clamped to 5 to 15 s; it runs from the camera's
#: landing (``WalkController.settled``)
DWELL_BASE_MS, DWELL_PER_WORD_MS, DWELL_MIN_MS, DWELL_MAX_MS = 2000, 300, 5000, 15000
#: the walk speed (the user's setting, or the agent's walkthrough_play ``speed``): what
#: it scales the reading time by
WALK_SPEEDS = {"slow": 1.5, "normal": 1.0, "fast": 0.6}
NORMAL = "normal"


def dwell_ms(text: str, speed: str = NORMAL) -> int:
    words = len(re.findall(r"[^\s*]+", text or ""))
    read = max(DWELL_MIN_MS, min(DWELL_MAX_MS, DWELL_BASE_MS + DWELL_PER_WORD_MS * words))
    return round(read * WALK_SPEEDS.get(speed, 1.0))


class WalkPlayback:
    def __init__(self) -> None:
        self.state = IDLE
        self.script: dict[str, Any] | None = None
        self.step = -1
        #: the user closed the walk while it was being prepared: a late play is refused
        self.cancelled = False
        #: the walk speed (``WALK_SPEEDS``)
        self.speed = NORMAL

    # -- reading ---------------------------------------------------------------------------
    @property
    def hop_count(self) -> int:
        return len(self.script["hops"]) if self.script else 0

    @property
    def last(self) -> int:
        return self.hop_count + 1

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    @property
    def target(self) -> str:
        return self.script["target"] if self.script else ""

    @property
    def hop(self) -> dict[str, Any]:
        if self.script and 1 <= self.step <= self.hop_count and self.state != PREPARING:
            return self.script["hops"][self.step - 1]
        return {}

    @property
    def caption(self) -> str:
        if not self.script or self.state == PREPARING or self.step < 0:
            return ""
        if self.step == 0:
            return self.script["intro"]
        if self.step == self.last:
            return self.script["summary"]
        return self.hop["caption"]

    @property
    def path_views(self) -> list[str]:
        if not self.script:
            return []
        out = ["project"]
        for h in self.script["hops"]:
            for v in (h["from"], h["to"]):
                if v not in out:
                    out.append(v)
        return out

    @property
    def focus_views(self) -> list[str]:
        hop = self.hop
        return [hop["from"], hop["to"]] if hop else self.path_views

    @property
    def dwell(self) -> int:
        return dwell_ms(self.caption, self.speed)

    # -- changes ---------------------------------------------------------------------------
    def prepare(self, script: dict[str, Any]) -> None:
        """The agent planned the walk and writes its captions: show the plan."""
        self.script, self.state, self.step, self.cancelled = script, PREPARING, -1, False

    def play_script(self, script: dict[str, Any]) -> None:
        """Play a finished script from the establishing shot, auto-advance on."""
        self.script, self.state, self.step, self.cancelled = script, PLAYING, 0, False

    def go_to(self, step: int) -> bool:
        """Make ``step`` current (clamped). Playing stays playing; reaching the summary
        stops auto-advance; leaving the summary pauses. False when nothing is playing."""
        if self.state not in (PLAYING, PAUSED, SUMMARY):
            return False
        self.step = max(0, min(self.last, step))
        if self.step == self.last:
            self.state = SUMMARY
        elif self.state == SUMMARY:
            self.state = PAUSED
        return True

    def forward(self) -> bool:
        if self.state not in (PLAYING, PAUSED) or self.step >= self.last:
            return False
        return self.go_to(self.step + 1)

    def back(self) -> bool:
        if self.state not in (PLAYING, PAUSED, SUMMARY) or self.step <= 0:
            return False
        return self.go_to(self.step - 1)

    def play(self) -> bool:
        """Auto-advance on; on the summary it is a replay."""
        if self.state == SUMMARY:
            return self.replay()
        if self.state != PAUSED:
            return False
        self.state = PLAYING
        return True

    def pause(self) -> bool:
        if self.state != PLAYING:
            return False
        self.state = PAUSED
        return True

    def replay(self) -> bool:
        if self.state not in (PLAYING, PAUSED, SUMMARY):
            return False
        self.state, self.step = PLAYING, 0
        return True

    def exit(self) -> bool:
        """Close the walk (x, Esc, Done); while preparing it cancels."""
        if not self.active:
            return False
        self.cancelled = self.state == PREPARING
        self.state, self.step = EXITED, -1
        return True


__all__ = ["ACTIVE", "NORMAL", "WALK_SPEEDS", "WalkPlayback", "dwell_ms"]
