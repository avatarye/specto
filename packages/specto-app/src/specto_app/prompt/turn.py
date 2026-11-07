"""One prompt turn (#44, spec #35 stories 11-16): the agent gets the prompt with the
canvas context, calls the six tools, and the strip gets one reply.

``TurnTools`` is what the agent's tools call into, whichever agent it is: it runs the
real ``AgentTools`` and keeps the turn's book. Every submit carries the turn's start
(``LiveCanvas.mark``), so a view or link the user changed mid-turn is not reverted: the
submit is refused with its live entry, which is no failed attempt (#98). Failed submits
(``CanvasInvalid``) return their fix-its to the agent and change nothing; after three in
one turn the agent is told to stop and report; a fourth is refused. A submit that
changed the canvas marks the turn ``done``: the session stops the model there, with no
closing reply (#98), and the strip shows the label. At the turn's end the successful submits are
squashed into one undo step (``LiveCanvas.squash``), so the strip's Undo reverts the
whole turn. ``save_skill`` / ``delete_skill`` (#161) go to the project's ``SkillStore``;
the turn keeps their ``SkillChange`` list for the strip, whose skill Undo reverts them in
one step; ``save_command`` / ``delete_command`` (#219) to its ``CommandStore`` and the
data tools (``data_write`` and friends, #219) to its ``DataStore`` likewise
(``CommandChange`` / ``DataChange``, the command and data rows' Undo). ``write_link_code``
/ ``read_link_code`` (#168) go to the project's
``LinkCodeStore`` likewise (``CodeChange``, the code row's Undo), the scratch pad tools
(#184) to the engine's ``ScratchStore`` (the turn's steps fold into one at its end, the
scratch row's Undo), ``list_files`` (#169) to
its ``ProjectFiles``, ``launchers`` / ``launch`` (#186) to the project's ``LaunchTools``
(each launch a ``Launched``, the thread's launch row; a refused one too, #192). ``walkthrough`` /
``walkthrough_play`` (#174) go to the window's ``WalkTools``; playing a walk ends the
turn like a submit, and a turn that ends while its walk is still being prepared plays
the template captions. ``fleet_status`` / ``fleet_send`` (#187) go to the window's
``FleetTools``, on a worker thread through ``acall`` (nimitz runs block); the turn keeps
every send, sent or refused, as a ``FleetSend`` for the thread's rows. ``run_turn`` drives an
``AgentSession`` (the Agent SDK, or the scripted fake) through one turn and returns the
``Reply``. No Qt here.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from specto_core.agent_tools import AgentTools
from specto_core.errors import SpectoError, TxnError
from specto_core.live_canvas import LiveCanvas
from specto_core.locks import is_user_origin

from ..launchers.agent import LAUNCH_TOOLS, LaunchTools
from ..launchers.book import Launched
from ..walk.tools import WALK_TOOLS, WalkTools
from .commands import COMMAND_TOOLS, CommandChange, CommandStore
from .context import CanvasContext, render_context
from .data import DATA_TOOLS, DataChange, DataStore
from .files import FILE_TOOLS, ProjectFiles
from .fleet import FLEET_TOOLS, FleetSend, FleetTools, SendRefused
from .link_code import LINK_CODE_TOOLS, CodeChange, LinkCodeStore
from .scratch import SCRATCH_TOOLS, ScratchTools, turn_label
from .skills import SKILL_TOOLS, SkillChange, SkillStore

MAX_ATTEMPTS = 3
DeltaFn = Callable[[str], None]


@dataclass
class ToolResult:
    """What a tool call returned to the agent: text (JSON for data) and the error flag."""

    name: str
    text: str
    is_error: bool = False
    data: Any = None
    ms: float = 0.0  # how long the call took (#73 timing)


@dataclass
class TurnOutcome:
    """What the session reports when the model's turn ends."""

    text: str = ""
    is_error: bool = False
    error: str = ""
    #: this turn's cost (the CLI reports the session's running total; the session
    #: keeps the delta) and that running total
    cost_usd: float | None = None
    session_cost_usd: float | None = None
    session_id: str = ""
    duration_ms: int = 0
    #: time in the model's API calls (the CLI's duration_api_ms)
    api_ms: int = 0
    #: time from the prompt to the first streamed token (-1: nothing streamed)
    first_token_ms: int = -1
    num_turns: int = 0
    #: the turn's output tokens (the result's usage, -1: not reported) and each model
    #: call's own, in order (#98's bench)
    output_tokens: int = -1
    call_output_tokens: list[int] = field(default_factory=list)


class AgentSession(Protocol):
    """The seam the Agent SDK sits behind. One session per window remembers the
    conversation; ``turn`` sends one prompt and streams the reply's text through
    ``on_delta`` until the model stops."""

    async def start(self) -> None: ...

    def set_model(self, model: str | None) -> None:
        """The model the next turn runs on (the prompt bar's chip, #73)."""
        ...

    async def turn(
        self,
        prompt: str,
        context_text: str,
        *,
        on_delta: DeltaFn | None = None,
        with_context: bool = True,
    ) -> TurnOutcome: ...

    async def interrupt(self) -> None: ...

    async def close(self) -> None: ...


@dataclass
class Reply:
    """What the reply strip shows for one turn."""

    text: str = ""
    #: one line: the turn's label (what changed), or the text when nothing changed
    summary: str = ""
    #: ``changed`` (one undo step), ``answered`` (no change), ``failed`` (gave up or error)
    status: str = "answered"
    turn_txn: str | None = None
    touched: list[str] = field(default_factory=list)
    attempts_failed: int = 0
    cost_usd: float | None = None
    session_cost_usd: float | None = None
    duration_ms: int = 0
    error: str = ""
    # -- timing (#73): the baseline for the latency work, one log line per turn
    #: wall time of the whole turn as the prompt bar saw it
    total_ms: int = 0
    first_token_ms: int = -1
    api_ms: int = 0
    #: every tool call of the turn, ``(name, ms)`` in order
    tool_calls: list[tuple[str, float]] = field(default_factory=list)
    #: output tokens of the turn (-1: not reported) and of each model call (#98)
    output_tokens: int = -1
    call_output_tokens: list[int] = field(default_factory=list)
    #: the project skills the turn saved or deleted, in order (#161)
    skills: list[SkillChange] = field(default_factory=list)
    #: those changes after the strip's Undo reverted them
    skills_undone: list[SkillChange] = field(default_factory=list)
    #: the project slash commands the turn saved or deleted (#219), and after their Undo
    commands: list[CommandChange] = field(default_factory=list)
    commands_undone: list[CommandChange] = field(default_factory=list)
    #: the agent's data files the turn wrote or deleted (#219), and after their Undo
    data_files: list[DataChange] = field(default_factory=list)
    data_files_undone: list[DataChange] = field(default_factory=list)
    #: the link code files the turn wrote, in order (#168), and after their Undo
    code: list[CodeChange] = field(default_factory=list)
    code_undone: list[CodeChange] = field(default_factory=list)
    #: the scratch pad changes the turn made (#184), their labels in order, the one
    #: store step they fold into, and whether the scratch row's Undo reverted it
    scratch: list[str] = field(default_factory=list)
    scratch_step: str | None = None
    scratch_undone: bool = False
    #: the turn's fleet_send calls, sent or refused, in order (#187)
    fleet: list[FleetSend] = field(default_factory=list)
    #: what the turn launched (#186) or was refused (#192), in order: each a ``Launched``
    #: whose note is "launched Blender 4.2 on a.blend" / "refused System default on ..."
    launches: list[Launched] = field(default_factory=list)
    #: the model got the prompt: False for an error with no assistant output (the CLI
    #: refused the turn, e.g. not logged in), so its context counts as unseen (SPEC-30)
    reached_agent: bool = True


class TurnTools:
    """The six agent tools with the turn's book-keeping around them, plus the skill
    tools when ``skills`` (the project's ``SkillStore``) is given, the command tools
    with ``commands``, the data tools with ``data``, the link code tools
    with ``link_code``, ``list_files`` with ``files``, the walk-through tools with
    ``walk`` (the window's ``WalkTools``), the fleet tools with ``fleet`` and the launcher
    tools with ``launch``."""

    def __init__(
        self,
        tools: AgentTools,
        live: LiveCanvas,
        *,
        origin: str = "agent:prompt",
        skills: SkillStore | None = None,
        commands: CommandStore | None = None,
        data: DataStore | None = None,
        link_code: LinkCodeStore | None = None,
        files: ProjectFiles | None = None,
        walk: WalkTools | None = None,
        scratch: ScratchTools | None = None,
        fleet: FleetTools | None = None,
        launch: LaunchTools | None = None,
    ):
        self.tools = tools
        self.live = live
        self.origin = origin
        self.skills = skills
        self.commands = commands
        self.data = data
        self.link_code = link_code
        self.files = files
        self.walk = walk
        self.scratch = scratch
        #: the scratch pad steps this turn made (#184) and where the turn began on its stack
        self.scratch_changes: list[str] = []
        self.scratch_start = scratch.store.cursor if scratch is not None else 0
        #: the step the turn's scratch changes folded into (``end``)
        self.scratch_step: str | None = None
        self.fleet = fleet
        self.launch = launch
        #: what this turn launched (#186)
        self.launches: list[Launched] = []
        #: the skill files this turn wrote or removed (#161)
        self.skill_changes: list[SkillChange] = []
        #: the command files and the data files this turn wrote or removed (#219)
        self.command_changes: list[CommandChange] = []
        self.data_changes: list[DataChange] = []
        #: the link code files this turn wrote (#168)
        self.code_changes: list[CodeChange] = []
        #: the messages this turn sent to the fleet, or tried to (#187)
        self.fleet_sends: list[FleetSend] = []
        self.start_cursor = live.cursor
        #: the turn's start in the canvas's changes (``LiveCanvas.mark``)
        self.since = live.mark()
        self.failures = 0
        self.labels: list[str] = []
        self.calls: list[ToolResult] = []
        self.active = False
        #: a submit changed the canvas this turn: the model stops there (#98)
        self.done = False
        #: told ``("tool_started" | "tool_finished", name)`` around every call (the
        #: agent icon's working state, #162)
        self.listener: Callable[[str, str], None] | None = None

    def begin(self) -> None:
        self.start_cursor = self.live.cursor
        self.since = self.live.mark()
        self.failures = 0
        self.labels = []
        self.calls = []
        self.skill_changes = []
        self.command_changes = []
        self.data_changes = []
        self.code_changes = []
        self.scratch_changes = []
        self.scratch_step = None
        if self.scratch is not None:
            self.scratch_start = self.scratch.store.cursor
        self.fleet_sends = []
        self.launches = []
        self.active = True
        self.done = False
        if self.walk is not None:
            self.walk.begin()

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Run one tool for the agent. Errors come back as results (never raised), so the
        agent reads the fix-it and retries inside the same turn."""
        args = dict(args or {})
        if self.listener is not None:
            self.listener("tool_started", name)
        try:
            return self._call(name, args)
        finally:
            if self.listener is not None:
                self.listener("tool_finished", name)

    async def acall(self, name: str, args: dict[str, Any]) -> ToolResult:
        """``call`` for the agents' async tool handlers: the fleet tools run nimitz on a
        worker thread (a status takes about 0.5 s), so the window keeps painting; the
        listener still hears both ends on the loop's own thread."""
        if name not in FLEET_TOOLS or self.fleet is None:
            return self.call(name, args)
        args = dict(args or {})
        if self.listener is not None:
            self.listener("tool_started", name)
        try:
            return await asyncio.to_thread(self._call, name, args)
        finally:
            if self.listener is not None:
                self.listener("tool_finished", name)

    def _call(self, name: str, args: dict[str, Any]) -> ToolResult:
        t0 = time.perf_counter()
        if name == "submit_canvas" and self.failures >= MAX_ATTEMPTS:
            res = ToolResult(
                name,
                "no more submits this turn: three documents were rejected already. Tell the "
                "user in one short line what you could not do.",
                is_error=True,
            )
            self.calls.append(res)
            return res
        if name == "submit_canvas":
            args["since"] = self.since
        try:
            if name in SKILL_TOOLS and self.skills is not None:
                change, data = self.skills.run(name, args)
                self.skill_changes.append(change)
            elif name in COMMAND_TOOLS and self.commands is not None:
                command, data = self.commands.run(name, args)
                self.command_changes.append(command)
            elif name in DATA_TOOLS and self.data is not None:
                written, data = self.data.run(name, args)
                if written is not None:
                    self.data_changes.append(written)
            elif name in LINK_CODE_TOOLS and self.link_code is not None:
                code, data = self.link_code.run(name, args)
                if code is not None:
                    self.code_changes.append(code)
            elif name in SCRATCH_TOOLS and self.scratch is not None:
                step, data = self.scratch.run(name, args, origin=self.origin)
                if step is not None:
                    self.scratch_changes.append(step.label)
            elif name in FILE_TOOLS and self.files is not None:
                data = self.files.run(name, args)
            elif name in WALK_TOOLS and self.walk is not None:
                data = self.walk.run(name, args)
            elif name in FLEET_TOOLS and self.fleet is not None:
                try:
                    send, data = self.fleet.run(name, args)
                except SendRefused as e:
                    self.fleet_sends.append(e.send)
                    raise
                if send is not None:
                    self.fleet_sends.append(send)
            elif name in LAUNCH_TOOLS and self.launch is not None:
                try:
                    launched, data = self.launch.run(name, args)
                except SpectoError as e:
                    if name == "launch":
                        self.launches.append(self.launch.refusal(args, e))
                    raise
                if launched is not None:
                    self.launches.append(launched)
            else:
                data = self.tools.run(name, args)
        except KeyError:
            res = ToolResult(name, f"unknown tool {name!r}", is_error=True)
        except SpectoError as e:
            res = ToolResult(name, self._error_text(name, e), is_error=True, data=e.to_dict())
        else:
            res = ToolResult(name, json.dumps(data, ensure_ascii=False, default=str), data=data)
            if name == "submit_canvas" and not data.get("empty"):
                self.labels.append(str(args.get("label") or data.get("label") or ""))
                self.done = True
            if name == "walkthrough_play":
                self.done = True  # the walk plays: no reply after it
        res.ms = (time.perf_counter() - t0) * 1000.0
        self.calls.append(res)
        return res

    def _error_text(self, name: str, e: SpectoError) -> str:
        text = str(e)
        if name != "submit_canvas" or e.code == "conflict":
            return text  # a clash with the user's mid-turn change is no failed attempt
        self.failures += 1
        if self.failures >= MAX_ATTEMPTS:
            text += (
                "\n\nThat was the third rejected document in this turn. Stop: do not submit "
                "again; tell the user in one short line what you could not do and why."
            )
        return text

    def end(self) -> tuple[str | None, list[str], str]:
        """Close the turn: squash its submits into one step. A step the user made
        mid-turn (a drag while the agent worked, #98) stays the user's own: only the
        turn's submits above the last such step are squashed. Returns ``(txn, touched,
        label)``; ``txn`` is ``None`` when the turn changed nothing."""
        self.active = False
        if self.walk is not None:
            self.walk.turn_ended()
        self.scratch_step = self._squash_scratch()
        label = next((lb for lb in reversed(self.labels) if lb), "") or "agent turn"
        start = self.start_cursor
        for i, rec in enumerate(self.live.history[start : self.live.cursor], start):
            if is_user_origin(rec.origin):
                start = i + 1
        try:
            rec = self.live.squash(start, label=label, origin=self.origin)
        except TxnError:
            return None, [], label  # the agent undid into or below the turn: leave it
        if rec is None:
            return None, [], label
        return rec.id, list(rec.touched), rec.label

    def _squash_scratch(self) -> str | None:
        """The turn's scratch pad steps as one (a step the user made meanwhile stays
        theirs); its id, None when the turn changed nothing there."""
        if self.scratch is None or not self.scratch_changes:
            return None
        store = self.scratch.store
        step = store.squash(
            self.scratch_start, label=turn_label(self.scratch_changes), origin=self.origin
        )
        return step.id if step is not None and not is_user_origin(step.origin) else None

    def undo_scratch(self, reply: Reply) -> bool:
        """The scratch row's Undo: the turn's scratch pad step, while it is still the
        newest one on the pad's stack."""
        if reply.scratch_step is None or self.scratch is None:
            return False
        store = self.scratch.store
        if store.top is None or store.top.id != reply.scratch_step:
            return False
        store.undo()
        reply.scratch_step, reply.scratch_undone = None, True
        return True

    def undo_turn(self, reply: Reply) -> bool:
        """The strip's Undo: revert the turn while its step is still the newest one."""
        if reply.turn_txn is None or self.live.top_id != reply.turn_txn:
            return False
        self.live.undo(origin="user")
        reply.turn_txn = None
        return True

    def undo_code(self, reply: Reply) -> bool:
        """The code row's Undo: every link code file the turn wrote, back as it was, one
        step; refused when a file changed since."""
        if not reply.code or self.link_code is None:
            return False
        if not self.link_code.undo(reply.code):
            return False
        reply.code, reply.code_undone = [], reply.code
        return True

    def undo_skills(self, reply: Reply) -> bool:
        """The strip's skill Undo: every skill change of the turn, one step; refused
        when a skill file changed since."""
        if not reply.skills or self.skills is None:
            return False
        if not self.skills.undo(reply.skills):
            return False
        reply.skills, reply.skills_undone = [], reply.skills
        return True

    def undo_commands(self, reply: Reply) -> bool:
        """The command row's Undo (#219): every command change of the turn, one step;
        refused when a command file changed since."""
        if not reply.commands or self.commands is None:
            return False
        if not self.commands.undo(reply.commands):
            return False
        reply.commands, reply.commands_undone = [], reply.commands
        return True

    def undo_data(self, reply: Reply) -> bool:
        """The data row's Undo (#219): every data file the turn wrote or deleted, back
        as it was, one step; refused when a file changed since."""
        if not reply.data_files or self.data is None:
            return False
        if not self.data.undo(reply.data_files):
            return False
        reply.data_files, reply.data_files_undone = [], reply.data_files
        return True


async def run_turn(
    session: AgentSession,
    tools: TurnTools,
    prompt: str,
    context: CanvasContext,
    *,
    on_delta: DeltaFn | None = None,
    with_context: bool = True,
) -> Reply:
    """One prompt through the session: the reply's text streams through ``on_delta``;
    the returned ``Reply`` carries the one-line summary, the turn's undo step and the
    views it touched."""
    tools.begin()
    t0 = time.perf_counter()
    first_token: list[int] = []  # when the session does not time it (the scripted one)

    def delta(piece: str) -> None:
        if not first_token:
            first_token.append(int((time.perf_counter() - t0) * 1000))
        if on_delta is not None:
            on_delta(piece)

    try:
        outcome = await session.turn(
            prompt, render_context(context), on_delta=delta, with_context=with_context
        )
    finally:
        txn, touched, label = tools.end()
    text = outcome.text.strip()
    reply = Reply(
        text=text,
        turn_txn=txn,
        touched=touched,
        attempts_failed=tools.failures,
        cost_usd=outcome.cost_usd,
        session_cost_usd=outcome.session_cost_usd,
        duration_ms=outcome.duration_ms,
        error=outcome.error,
        total_ms=int((time.perf_counter() - t0) * 1000),
        first_token_ms=(
            outcome.first_token_ms if outcome.first_token_ms >= 0 else (first_token or [-1])[0]
        ),
        api_ms=outcome.api_ms,
        tool_calls=[(c.name, c.ms) for c in tools.calls],
        output_tokens=outcome.output_tokens,
        call_output_tokens=list(outcome.call_output_tokens),
        skills=list(tools.skill_changes),
        commands=list(tools.command_changes),
        data_files=list(tools.data_changes),
        code=list(tools.code_changes),
        scratch=list(tools.scratch_changes),
        scratch_step=tools.scratch_step,
        fleet=list(tools.fleet_sends),
        launches=list(tools.launches),
        reached_agent=not (outcome.is_error and not text and not tools.calls),
    )
    if outcome.is_error:
        reply.status = "failed"
        reply.summary = text or outcome.error or "the agent's turn failed"
    elif txn is not None:
        reply.status = "changed"
        reply.summary = label
        reply.text = reply.text or label  # no closing reply (#98): the label is the reply
    elif tools.failures >= MAX_ATTEMPTS:
        reply.status = "failed"
        reply.summary = text or "the agent could not do that"
    elif tools.walk is not None and tools.walk.played:
        reply.status = "answered"
        reply.summary = text.splitlines()[0] if text else f"walk-through: {tools.walk.played}"
        reply.text = reply.text or reply.summary
    else:
        reply.status = "answered"
        reply.summary = text.splitlines()[0] if text else ""
    return reply


def timing_line(reply: Reply) -> str:
    """One line for launch.log: total, first token, model (API) time, every tool call
    with its ms, and the turn's own cost next to the session's running total."""
    parts = [f"total {reply.total_ms / 1000:.1f} s"]
    if reply.first_token_ms >= 0:
        parts.append(f"first token {reply.first_token_ms / 1000:.1f} s")
    if reply.api_ms:
        parts.append(f"model {reply.api_ms / 1000:.1f} s")
    calls = ", ".join(f"{name} {ms:.0f} ms" for name, ms in reply.tool_calls) or "none"
    parts.append(f"tools [{calls}]")
    if reply.output_tokens >= 0:
        per = "+".join(str(n) for n in reply.call_output_tokens)
        parts.append(f"{reply.output_tokens} output tokens" + (f" ({per})" if per else ""))
    if reply.cost_usd is not None:
        session = (
            f" ({reply.session_cost_usd:.3f} session)" if reply.session_cost_usd is not None else ""
        )
        parts.append(f"{reply.cost_usd:.3f} USD this turn{session}")
    return "; ".join(parts)


__all__ = [
    "MAX_ATTEMPTS",
    "AgentSession",
    "Reply",
    "ToolResult",
    "TurnOutcome",
    "TurnTools",
    "run_turn",
    "timing_line",
]
