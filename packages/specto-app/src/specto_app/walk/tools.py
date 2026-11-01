"""The walk-through's two agent tools (#174), for the prompt bar's session only (not
MCP): ``walkthrough {view}`` plans the walk and shows it being prepared, and returns the
planned hops with their facts so the agent writes every caption in one pass;
``walkthrough_play {view, captions, ...}`` fills the script and plays it. A turn that
ends while the walk is still being prepared plays the template captions. No Qt here:
the ``WalkPlayer`` (``WalkController`` in the app) does the showing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from specto_core.agent_tools import ToolSpec
from specto_core.walkthrough import WalkError, caption_script, walk_script

from .playback import WALK_SPEEDS

WALK_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "walkthrough",
            "Plan a walk-through of how a view's data reaches it from the project source, "
            "for 'walk me through <view>'. Shows the user that the walk is being prepared "
            "and returns the hops in the order they play (forks: each extra input right "
            "after its fork, branch true), each with its link kind, summary, note, counts "
            "in and out and a template caption, plus the forks. Then write every caption in "
            "one pass and call walkthrough_play once.",
            read_only=True,
            params={
                "type": "object",
                "properties": {
                    "view": {"type": "string", "description": "the view's name: pola"},
                },
                "required": ["view"],
            },
        ),
        ToolSpec(
            "walkthrough_play",
            "Play the walk-through planned by walkthrough: one caption per hop in hop order, "
            "each one or two plain sentences saying what the link does for every item and "
            "what arrives (names and numbers in **bold**; no item is followed), an intro for "
            "the establishing shot, a summary, and optional chips (a few words per hop: what "
            "arrives at its view, '128 frames'). An empty caption keeps the template. Pass "
            "speed only when the user asks for a slower or faster walk: it is their walk "
            "speed setting from then on. It plays at once and ends your turn: write no reply "
            "after it.",
            params={
                "type": "object",
                "properties": {
                    "view": {"type": "string", "description": "the view walked to"},
                    "captions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "one per hop, in hop order",
                    },
                    "intro": {"type": "string", "description": "the establishing shot"},
                    "summary": {"type": "string", "description": "the closing summary"},
                    "chips": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "optional, one per hop: what arrives, a few words",
                    },
                    "speed": {
                        "type": "string",
                        "enum": list(WALK_SPEEDS),
                        "description": "optional: the user's walk speed (how long each step "
                        "stays to be read)",
                    },
                },
                "required": ["view", "captions"],
            },
        ),
    )
}

#: the agent's view of a hop: what it writes captions from (the UI's rects stay out)
_AGENT_FIELDS = (
    "index",
    "link",
    "from",
    "to",
    "kind",
    "kind_label",
    "summary",
    "note",
    "count_in",
    "count_out",
    "branch",
    "fork",
    "status",
    "params",
    "caption",
)


_REQUEST = re.compile(
    r"^\s*(?:please\s+)?walk\s+(?:me\s+)?through\s+(?:the\s+)?(?:view\s+)?([A-Za-z]+)\b",
    re.IGNORECASE,
)


def walk_request(text: str, data: Mapping[str, Any]) -> str | None:
    """The view a "walk me through <view>" request names, when it is a view on the
    canvas; ``None`` for anything else (the prompt bar plays it itself when there is no
    agent)."""
    m = _REQUEST.match(text or "")
    if m is None:
        return None
    name = m.group(1).lower()
    return name if name in (data.get("views") or {}) else None


class WalkPlayer(Protocol):
    """What shows the walk (the app's ``WalkController``)."""

    def prepare(self, script: dict[str, Any]) -> None: ...

    def play_script(self, script: dict[str, Any]) -> None:
        """Play a captioned script; raises ``WalkError`` when the user cancelled it."""
        ...

    def turn_ended(self) -> None:
        """The prompt turn ended: a walk still being prepared plays its templates."""
        ...

    def set_speed(self, speed: str) -> None:
        """The user's walk speed (slow, normal, fast), kept for the walks after."""
        ...


class WalkTools:
    """The two tools over the live document and link runtime (``source()`` returns
    ``(data, links)``) and a ``WalkPlayer``."""

    def __init__(self, source: Callable[[], tuple[Mapping[str, Any], Any]], player: WalkPlayer):
        self.source = source
        self.player = player
        #: the view the last ``walkthrough_play`` of the turn played (the reply strip)
        self.played: str | None = None
        self._planned: dict[str, Any] | None = None

    def begin(self) -> None:
        self.played = None

    def run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "walkthrough":
            return self.walkthrough(**args)
        if name == "walkthrough_play":
            return self.walkthrough_play(**args)
        raise KeyError(name)

    def _plan(self, view: str) -> dict[str, Any]:
        data, links = self.source()
        return walk_script(data, view, links)

    def walkthrough(self, view: str) -> dict[str, Any]:
        script = self._plan(view)
        self._planned = script
        self.player.prepare(script)
        return {
            "target": script["target"],
            "hop_count": len(script["hops"]),
            "project": {"count": script["project"]["count"]},
            "forks": script["forks"],
            "not_connected": script["not_connected"],
            "hops": [{k: h[k] for k in _AGENT_FIELDS} for h in script["hops"]],
            "intro": script["intro"],
            "summary": script["summary"],
            "next": f"call walkthrough_play with {len(script['hops'])} captions, in hop order",
        }

    def walkthrough_play(
        self,
        view: str,
        captions: list[str],
        intro: str | None = None,
        summary: str | None = None,
        chips: list[str] | None = None,
        speed: str | None = None,
    ) -> dict[str, Any]:
        if speed is not None and speed not in WALK_SPEEDS:
            raise WalkError(f"no walk speed {speed!r}", fix="use slow, normal or fast, or none")
        planned = self._planned
        script = planned if planned is not None and planned["target"] == view else None
        if script is None:
            script = self._plan(view)
        played = caption_script(script, list(captions), intro=intro, summary=summary, chips=chips)
        if speed is not None:
            self.player.set_speed(speed)
        self.player.play_script(played)
        self._planned = None
        self.played = view
        return {"playing": view, "hops": len(played["hops"])}

    def turn_ended(self) -> None:
        self._planned = None
        self.player.turn_ended()


__all__ = ["WALK_TOOLS", "WalkPlayer", "WalkTools", "walk_request"]
