"""A scripted agent behind the ``AgentSession`` seam: no model, real tools. Tests and
the GUI flows give it one list of steps per turn; it runs them in order through
``TurnTools`` and streams its text word by word.

Steps: ``{"tool": name, "args": {...}}`` (``args.document_file`` loads a JSON document
relative to the script file; ``args.text_file`` loads a text file there as ``text``,
#219), ``{"skill": "specto:<name>"}`` (reads the project skill
as the Skill tool would, into ``skills_used``; #161), ``{"text": "..."}`` and
``{"wait_ms": n}`` (the agent "thinks" for n ms: a flow or test acts mid-turn, #98). Script file:
``{"turns": [[step, ...], ...]}``; a turn beyond the script answers that nothing is
scripted. ``received`` keeps every prompt and context block, ``calls`` every tool result.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .turn import DeltaFn, ToolResult, TurnOutcome, TurnTools


class ScriptedAgentSession:
    def __init__(
        self,
        tools: TurnTools,
        turns: list[list[dict[str, Any]]],
        *,
        base: Path | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self.tools = tools
        self.turns = [list(t) for t in turns]
        self.base = base
        self.delay_s = delay_s
        self.received: list[dict[str, Any]] = []
        self.calls: list[ToolResult] = []
        self.started = False
        self.closed = False
        self.info: dict[str, Any] = {"model": "scripted", "api_key_source": "none"}
        self.models: list[str | None] = []  # every set_model, in order
        #: every skill a ``skill`` step read: ``(name, SKILL.md text or None: no such skill)``
        self.skills_used: list[tuple[str, str | None]] = []

    @classmethod
    def from_file(cls, path: Path, tools: TurnTools) -> Any:
        """``{"turns": [...], "delay_ms": 30}``; the delay paces the streamed words."""
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
        delay_s = float(spec.get("delay_ms", 30)) / 1000.0
        return cls(tools, spec["turns"], base=Path(path).parent, delay_s=delay_s)

    async def start(self) -> None:
        self.started = True

    def set_model(self, model: str | None) -> None:
        self.models.append(model)
        self.info["model"] = model or "scripted"

    async def turn(
        self,
        prompt: str,
        context_text: str,
        *,
        on_delta: DeltaFn | None = None,
        with_context: bool = True,
    ) -> TurnOutcome:
        self.received.append(
            {"prompt": prompt, "context": context_text, "with_context": with_context}
        )
        steps = self.turns.pop(0) if self.turns else [{"text": "Nothing scripted for this turn."}]
        text: list[str] = []
        for step in steps:
            if "wait_ms" in step:
                await asyncio.sleep(float(step["wait_ms"]) / 1000.0)
            if "skill" in step:
                self.skills_used.append((step["skill"], self._skill(step["skill"])))
            if "tool" in step:
                args = self._args(step.get("args") or {})
                self.calls.append(await self.tools.acall(step["tool"], args))
            if "text" in step:
                for piece in _pieces(step["text"]):
                    text.append(piece)
                    if on_delta is not None:
                        on_delta(piece)
                    if self.delay_s:
                        await asyncio.sleep(self.delay_s)
                    else:
                        await asyncio.sleep(0)
        return TurnOutcome(text="".join(text), session_id="scripted", num_turns=1)

    def _skill(self, name: str) -> str | None:
        store = self.tools.skills
        if store is None:
            return None
        plugin, _, short = name.rpartition(":")
        where = ".specto" if plugin else ".claude"
        try:
            return (store.root / where / "skills" / short / "SKILL.md").read_text(encoding="utf-8")
        except OSError:
            return None

    def _args(self, args: dict[str, Any]) -> dict[str, Any]:
        args = dict(args)
        file = args.pop("document_file", None)
        if file is not None:
            args["document"] = json.loads(self._file(file).read_text(encoding="utf-8"))
        file = args.pop("text_file", None)
        if file is not None:
            args["text"] = self._file(file).read_text(encoding="utf-8")
        return args

    def _file(self, file: str) -> Path:
        path = Path(file)
        if not path.is_absolute() and self.base is not None:
            path = self.base / path
        return path

    async def interrupt(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


def _pieces(text: str) -> list[str]:
    """Words with their trailing space, so the strip fills in as a model's would."""
    out: list[str] = []
    for word in text.split(" "):
        out.append(word + " ")
    out[-1] = out[-1][:-1]
    return [p for p in out if p]


__all__ = ["ScriptedAgentSession"]
