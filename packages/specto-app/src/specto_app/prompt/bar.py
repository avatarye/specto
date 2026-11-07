"""``PromptBar``: the QML-facing object behind the prompt bar and the reply strip
(#44, spec #35 stories 8-16). It owns the window's one agent session (made lazily by
the factory the app chose: Claude Code over the Agent SDK, or a scripted agent) and
runs each prompt as one ``run_turn`` task on the app's qasync loop.

What the QML binds to: ``busy`` / ``status`` (``idle``, ``working``, ``changed``,
``answered``, ``failed``), ``replyText`` (streaming), ``summary`` (one line),
``canUndoTurn``, ``turnDiff`` (the turn's changes, one line each, shown on request:
story 15); what it calls: ``submit(text)``, ``undoTurn()``, ``interrupt()``,
``dismiss()``, and ``setMouse`` / ``clearMouse`` from the canvas area so the context
carries the pointer. ``touched`` fires with the view ids a turn changed (the slots
outline them briefly). ``model`` / ``setModel`` is the model chip (#73): one of
``MODELS``, applied to the session before its next turn and remembered per user in the
app settings (never in the canvas document). A turn that saved or deleted project skills
(#161) adds ``skillNote`` ("saved skill specto:<name>") with Open (``skillMarkdown``, the
file rendered as markdown) and ``undoSkills()`` (every skill change of the turn, one
step); a skill change the agent did not make itself reaches it in the next context.
Link code the turn wrote (#168) adds ``codeNote`` ("wrote link code <name>: needs your
approval") with Open (``codeMarkdown``, the file as a code block) and ``undoCode()``.
Slash commands the turn saved or deleted (#219) add a command row (``commandNote``,
"saved command /<name>", Open and ``undoCommandsAt``); data files it wrote or deleted
under ``.specto/`` (#219) add a data row (``dataNote``, "wrote .specto/layouts/<name>.json",
Open and ``undoDataAt``).
Scratch pad changes the turn made (#184) add a scratch row ("scratch pad: <what>") whose
Undo (``undoScratchAt``) reverts them in one step while it is the pad's newest; every
prompt's context names the pad's items (labels only).
The context's files summary comes from a walk of the project root (``ProjectFiles``,
#169), the same listing the agent's ``list_files`` reads. Each ``fleet_send`` of a turn
(#187) adds a ``fleet`` row; ``fleetRecipients`` / ``setFleetRecipients`` is the user's
allowed-recipients setting (empty: every fleet nimitz reports).

The agent panel (#162) shows the bar as a floating agent icon (``icon``: idle,
thinking, working, needs you, error) and a conversation thread (``thread``: every user
turn, reply, turn row with its own Undo and diff, skill row and session divider, kept
in ``.specto/agent-thread.jsonl``) that opens on hover (``panel``: its open, pin and
size rules). ``newSession()`` starts a fresh agent session below a divider;
``slashCommands`` completes ``/``; ``clipboardMarkdown`` turns a formatted paste into
markdown.

Peer requests (#187): ``receive_peer`` (the endpoint's ``message`` command) adds a peer
row and runs the request as a turn labelled with the sender, queues it behind the
running turn, or (``peerRequests`` "ask") waits for the row's Run (``runPeer``) or
Dismiss (``dismissPeer``); "off" refuses it. ``peer`` is the running peer turn's
``{from, reply_to, id}`` (None for a typed one). ``instance_name`` is the window's
instance name (``specto_core.instances``).

Approval requests (#203, ADR 0006): ``ask_approval`` (the endpoint's ``link.approve``
command, through ``engine.host.approvals``) adds an ``approval`` card to the thread
naming the requester, the file and the link; nothing is approved until the user clicks
its Approve (``approveLink``: the file as it is now, refused when it changed since the
request), or Dismiss (``dismissApproval``). The card closes by itself when the user
approves the file in the view's header instead.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QSettings, Signal, Slot

from specto_core import CallContext
from specto_core.agent_tools import AgentTools
from specto_core.diff_text import describe_patch
from specto_core.errors import SpectoError
from specto_core.instances import sender_verified
from specto_core.links import FilesValue, Selection
from specto_core.ports import PROJECT_ID

from .. import clipboard
from ..launchers.agent import LaunchTools
from ..launchers.book import book_for
from ..walk.tools import WalkTools, walk_request
from .commands import CommandStore, list_commands
from .context import CanvasContext, document_hash
from .data import DataStore
from .files import ProjectFiles
from .fleet import PATH_RE, RECIPIENTS_SETTING, FleetTools, Nimitz
from .link_code import LinkCodeStore
from .panel import AgentIcon, AgentPanel, MarkdownHighlighter, ThreadModel
from .peers import (
    PEER_MODE_DETAILS,
    PEER_MODES,
    PeerRefused,
    check_message,
    peer_prompt,
    save_peer_mode,
    saved_peer_mode,
)
from .scratch import ScratchTools, context_items, turn_label
from .session import (
    DEFAULT_MODEL,
    MODEL_DETAILS,
    MODEL_LABELS,
    MODELS,
    SERVER_NAME,
    SESSION_FILE,
    project_commands,
    project_skills,
    slash_command,
)
from .skills import SkillStore, list_skills, parse_frontmatter, skills_key
from .thread import REQUESTS, AgentThread, Entry, html_to_markdown, patch_counts
from .turn import AgentSession, Reply, TurnTools, run_turn, timing_line

if TYPE_CHECKING:
    from specto_app.app import SpectoApp

SessionFactory = Callable[[TurnTools], AgentSession]
ORIGIN = "agent:prompt"
MAX_PATHS = 50  # selected files listed in the context
MODEL_SETTING = "prompt/model"  # the chip's choice, in the user's settings ini
#: Claude Code's own commands: listed greyed in the completion, refused on submit
BUILT_IN_COMMANDS = (
    "clear",
    "compact",
    "config",
    "cost",
    "help",
    "init",
    "memory",
    "model",
    "review",
    "status",
)


def saved_model() -> str:
    """The model the user chose last (``MODELS``), ``DEFAULT_MODEL`` when none or
    when the saved name is no longer one of the three."""
    value = str(QSettings().value(MODEL_SETTING, DEFAULT_MODEL) or DEFAULT_MODEL)
    return value if value in MODELS else DEFAULT_MODEL


def save_model(name: str) -> None:
    settings = QSettings()
    settings.setValue(MODEL_SETTING, name)
    settings.sync()


def saved_recipients() -> list[str] | None:
    """The nimitz paths ``fleet_send`` may message (#187), ``None`` when the user set
    none (then every fleet nimitz reports, ``F/...`` each). This setting and the thread's
    row per send are the user's consent: each send passes nimitz's own gate with --yes."""
    try:
        value = json.loads(str(QSettings().value(RECIPIENTS_SETTING, "") or "null"))
    except ValueError:
        return None
    if not isinstance(value, list) or not value:
        return None
    return [str(p) for p in value]


def save_recipients(paths: list[str]) -> None:
    settings = QSettings()
    settings.setValue(RECIPIENTS_SETTING, json.dumps(list(paths)) if paths else "")
    settings.sync()


class PromptBar(QObject):
    statusChanged = Signal()
    replyChanged = Signal()
    touched = Signal(list)  # view ids the finished turn changed
    turnFinished = Signal()
    prefillRequested = Signal(str)  # text to put in the field, focused (the QML bar listens)
    draftChanged = Signal()
    modelChanged = Signal()
    recipientsChanged = Signal()
    sessionStarted = Signal()  # the panel's new-session button started one
    peerRequestsChanged = Signal()

    # class-level defaults: QML bindings may read a bar whose Python side is being torn down
    reply: Reply | None = None
    _text = ""
    _status = "idle"
    _draft = ""
    _model = DEFAULT_MODEL
    _peer_mode = "auto"
    peer: dict[str, Any] | None = None

    def __init__(self, app: SpectoApp, factory: SessionFactory, parent: QObject | None = None):
        super().__init__(parent)
        self.app = app
        self.factory = factory
        self.live = app.engine.live
        ctx = CallContext(connection="prompt-bar", origin=ORIGIN)
        #: the walk-through (#174): its tools play through the app's controller
        self.walkthrough = getattr(app, "walkthrough", None)
        walk = None
        if self.walkthrough is not None:
            walk = WalkTools(lambda: (app.engine.live.data, app.engine.links), self.walkthrough)
        self.tools = TurnTools(
            AgentTools(lambda name, args: app.engine.call(name, args, ctx)),
            self.live,
            origin=ORIGIN,
            skills=SkillStore(app.project.root),
            commands=CommandStore(app.project.root),
            data=DataStore(app.project.root),
            link_code=LinkCodeStore(app.project.root, engine=app.engine),
            files=ProjectFiles(app.project.root, engine=app.engine),
            walk=walk,
            scratch=ScratchTools(
                lambda: app.engine.scratch,
                to_png=_to_png,
                has_view=lambda: any(
                    v.get("type") == "scratch" for v in app.engine.live.data["views"].values()
                ),
            ),
            fleet=FleetTools(
                Nimitz(),
                instance=self._instance,
                project=str(app.project.root),
                recipients=saved_recipients,
                peer=lambda: getattr(self, "peer", None),
            ),
            launch=LaunchTools(book_for(app.project.root)),
        )
        self.session: AgentSession | None = None
        self.reply: Reply | None = None
        self.replies: list[Reply] = []
        self._text = ""
        self._status = "idle"
        self._task: asyncio.Task[None] | None = None
        self._mouse: tuple[float, float] | None = None
        self._draft = ""
        self._model = saved_model()
        self._last_hash = ""  # the document hash the agent last saw
        #: the project skills as the agent last knew them (#161): the session listed
        #: them at its start, its own saves reported back to it
        self._skills_seen = skills_key(app.project.root)
        self._off_patch = self.live.on_patch(lambda patch, meta: self._canvas_changed())
        # a scratch row's Undo lasts while its step is the pad's newest (#184)
        self._off_scratch = app.engine.scratch.on_change(lambda step, kind: self._canvas_changed())
        # the agent panel (#162)
        self._icon = AgentIcon(self)
        self._panel = AgentPanel(app.project.root, self)
        self._panel.opened.connect(self._icon.panel_opened)
        self._thread = ThreadModel(AgentThread(app.project.root), live=self._live, parent=self)
        #: turn and skill rows of this launch: entry id -> (reply, the turn's undo step)
        self._rows: dict[int, tuple[Reply, str | None]] = {}
        self._agent_entry: Entry | None = None
        self.tools.listener = self._tool_event
        # peer requests (#187)
        self.instance_name = str(getattr(app, "instance_name", "") or "")
        self._peer_mode = saved_peer_mode()
        #: the running peer turn's ``{from, reply_to, id}``; None for a typed turn
        self.peer = None
        #: peer rows waiting for the user's Run or Dismiss ("ask"), by entry id
        self._asked: dict[int, Entry] = {}
        #: peer rows to run once the running turn ends, oldest first
        self._queue: list[Entry] = []
        #: approval cards waiting for the user's Approve or Dismiss (#203), by entry id
        self._approvals: dict[int, Entry] = {}
        # a link approved elsewhere (the view's header) retires its card
        self._off_events = self.live.events.subscribe(self._link_event)
        unavailable = str(getattr(factory, "unavailable", "") or "")
        if unavailable:
            self._icon.unavailable(unavailable)

    def _instance(self) -> str:
        """This window's instance name (#187 section 1, ``SpectoApp.instance_name``)."""
        return str(getattr(self.app, "instance_name", "") or "unnamed")

    def _canvas_changed(self) -> None:
        self.statusChanged.emit()
        self._thread.refresh_live()

    def _tool_event(self, event: str, name: str) -> None:
        if event == "tool_started":
            self._icon.tool_started(name)
        else:
            self._icon.tool_finished(name)

    # -- the agent panel (#162) ----------------------------------------------------------------
    @Property(QObject, constant=True)
    def icon(self) -> AgentIcon:
        return self._icon

    @Property(QObject, constant=True)
    def panel(self) -> AgentPanel:
        return self._panel

    @Property(QObject, constant=True)
    def thread(self) -> ThreadModel:
        return self._thread

    def _applied(self, txn: str | None) -> bool:
        applied = self.live.history[: self.live.cursor]
        return txn is not None and any(rec.id == txn for rec in applied)

    def _live(self, entry: Entry) -> dict[str, Any]:
        """What only this launch knows of a row: its Undo (a turn while its step is the
        newest; a skill change until undone), whether it was undone, and whether it is
        the latest turn's row of its kind (the flows' ``replyUndo`` and friends): the
        last of its kind with no request after it, so a turn that adds no such row takes
        the names away from the earlier one (#164)."""
        out: dict[str, Any] = {}
        row = self._rows.get(entry.id)
        if entry.kind == "turn" and row is not None:
            txn = row[1]
            out["canUndo"] = txn is not None and self.live.top_id == txn
            out["undone"] = not self._applied(txn)
        elif entry.kind == "skill" and row is not None:
            reply = row[0]
            out["canUndo"] = bool(reply.skills)
            out["undone"] = bool(reply.skills_undone)
        elif entry.kind == "command" and row is not None:
            reply = row[0]
            out["canUndo"] = bool(reply.commands)
            out["undone"] = bool(reply.commands_undone)
        elif entry.kind == "data" and row is not None:
            reply = row[0]
            out["canUndo"] = bool(reply.data_files)
            out["undone"] = bool(reply.data_files_undone)
        elif entry.kind == "code" and row is not None:
            reply = row[0]
            out["canUndo"] = bool(reply.code)
            out["undone"] = bool(reply.code_undone)
        elif entry.kind == "scratch" and row is not None:
            reply = row[0]
            top = self.app.engine.scratch.top if self.app is not None else None
            step = reply.scratch_step
            out["canUndo"] = step is not None and top is not None and top.id == step
            out["undone"] = reply.scratch_undone
        elif entry.kind == "peer":
            out["canRun"] = entry.id in self._asked
        elif entry.kind == "approval":
            out["canRun"] = entry.id in self._approvals
        if entry.kind in ("turn", "skill", "command", "data", "code", "scratch", "fleet", "launch"):
            entries = self._thread.thread.entries
            kinds = (entry.kind, *REQUESTS)
            last = next((e for e in reversed(entries) if e.kind in kinds), None)
            out["latest"] = last is entry
        return out

    def _add(self, kind: str, **values: Any) -> Entry:
        entry = self._thread.thread.add(kind, **values)
        self._thread.sync()
        self._thread.refresh_live()  # a request ends the earlier rows' "latest"
        return entry

    @Slot(int, result=bool)
    def undoTurnAt(self, entry_id: int) -> bool:
        """A turn row's Undo: that turn, while its step is still the newest."""
        row = self._rows.get(int(entry_id))
        if row is None:
            return False
        reply, txn = row
        if txn is None or self.live.top_id != txn:
            return False
        reply.turn_txn = txn
        return self._undo_turn(reply)

    def _undo_turn(self, reply: Reply) -> bool:
        try:
            ok = self.tools.undo_turn(reply)
        except SpectoError as e:
            self.app.notices.error(e.message, fix=e.fix, code=e.code, command="undo")
            return False
        self.statusChanged.emit()
        self._thread.refresh_live()
        return ok

    @Slot(int, result=bool)
    def undoSkillsAt(self, entry_id: int) -> bool:
        """A skill row's Undo: that turn's skill files back as they were."""
        row = self._rows.get(int(entry_id))
        return self._undo_skills(row[0]) if row is not None else False

    @Slot(int, result=str)
    def skillMarkdownAt(self, entry_id: int) -> str:
        """A skill row's Open: its turn's (last) skill file as markdown; a code row's
        Open (#168): its turn's (last) link code file as a code block."""
        entries = self._thread.thread.entries
        entry = next((e for e in entries if e.id == int(entry_id)), None)
        row = self._rows.get(int(entry_id))
        if entry is not None and entry.kind == "code":
            if row is not None:
                return self._code_markdown(row[0])
            return _code_file_markdown(entry.path) if entry.path else ""
        if entry is not None and entry.kind == "approval":
            return _code_file_markdown(entry.path) if entry.path else ""
        if entry is not None and entry.kind == "command":
            if row is not None:
                return self._command_markdown(row[0])
            return _command_file_markdown(entry.label.rpartition(" ")[2], entry.path)
        if entry is not None and entry.kind == "data":
            if row is not None:
                return self._data_markdown(row[0])
            return _data_file_markdown(entry.path) if entry.path else ""
        if row is not None:
            return self._skill_markdown(row[0])
        if entry is None or not entry.path:
            return ""
        return _skill_file_markdown(entry.label.rpartition(" ")[2], entry.path)

    @Slot(int, result=bool)
    def undoCommandsAt(self, entry_id: int) -> bool:
        """A command row's Undo (#219): that turn's command files back as they were."""
        row = self._rows.get(int(entry_id))
        return self._undo_commands(row[0]) if row is not None else False

    @Slot(int, result=bool)
    def undoDataAt(self, entry_id: int) -> bool:
        """A data row's Undo (#219): that turn's data files back as they were."""
        row = self._rows.get(int(entry_id))
        return self._undo_data(row[0]) if row is not None else False

    @Slot(int, result=bool)
    def undoCodeAt(self, entry_id: int) -> bool:
        """A code row's Undo: that turn's link code files back as they were."""
        row = self._rows.get(int(entry_id))
        return self._undo_code(row[0]) if row is not None else False

    @Slot(int, result=bool)
    def undoScratchAt(self, entry_id: int) -> bool:
        """A scratch row's Undo (#184): that turn's scratch pad changes, one step, while
        it is the pad's newest step."""
        row = self._rows.get(int(entry_id))
        if row is None or not self.tools.undo_scratch(row[0]):
            return False
        self.statusChanged.emit()
        self._thread.refresh_live()
        return True

    @Slot(result=bool)
    def newSession(self) -> bool:
        """The panel's new-session button: the agent forgets the conversation (a fresh
        Claude Code session, and the next launch resumes that one); the thread keeps the
        old one readable above a divider. Refused while a turn runs."""
        if self.busy:
            self.app.notices.info("the agent is still working on the last request")
            return False
        session, self.session = self.session, None
        if session is not None:
            loop = self.app.loop or asyncio.get_event_loop()
            loop.create_task(session.close())
        (Path(self.app.project.root) / ".specto" / SESSION_FILE).unlink(missing_ok=True)
        self._last_hash = ""  # the new session gets the whole document
        self._skills_seen = ""  # and the project's skills
        self.reply = None
        self._text = ""
        self._status = "idle"
        self._add("divider", text="New session")
        self.replyChanged.emit()
        self.statusChanged.emit()
        self.sessionStarted.emit()
        return True

    @Slot(str, result=list)
    def slashCommands(self, prefix: str) -> list[dict[str, Any]]:
        """The ``/`` completion: the project's skills and commands matching ``prefix``
        (``{name, description, kind, available}``), then Claude Code's own commands,
        greyed: not available in Specto (``submit`` refuses them)."""
        root = Path(self.app.project.root)
        prefix = str(prefix).lstrip("/").lower()
        out: list[dict[str, Any]] = []
        for kind, names in (("skill", project_skills(root)), ("command", project_commands(root))):
            for name in names:
                low = name.lower()
                if low.startswith(prefix) or low.rpartition(":")[2].startswith(prefix):
                    desc = _description(root, kind, name)
                    out.append({"name": name, "description": desc, "kind": kind, "available": True})
        for name in BUILT_IN_COMMANDS:
            if name.startswith(prefix):
                out.append(
                    {
                        "name": name,
                        "description": "Built-in: not available in Specto",
                        "kind": "built-in",
                        "available": False,
                    }
                )
        return out

    @Slot(bool, result=str)
    def clipboardMarkdown(self, plain: bool) -> str:
        """The clipboard for a paste into the input: formatted text (HTML) as markdown,
        or its plain text when ``plain`` (Ctrl+Shift+V) or when it has no formatting."""
        mime = clipboard.mime_data()
        if mime is None:
            return ""
        if not plain and mime.hasHtml():
            md = html_to_markdown(mime.html())
            if md:
                return md
        return mime.text() if mime.hasText() else ""

    @Slot(result=bool)
    def clipboardHasFormatting(self) -> bool:
        mime = clipboard.mime_data()
        return mime is not None and mime.hasHtml()

    @Slot(QObject)
    def highlight(self, quick_document: QObject) -> None:
        """Style the input's markdown in place (``MarkdownHighlighter``)."""
        from .. import theme as _theme

        doc = quick_document.textDocument() if hasattr(quick_document, "textDocument") else None
        if doc is None:
            return
        self._highlighter = MarkdownHighlighter(doc, _theme.COLORS, _theme.FONTS["mono"])

    @Slot(str)
    def copyText(self, text: str) -> None:
        """A code block's Copy."""
        clipboard.set_text(str(text))

    # -- QML properties ------------------------------------------------------------------------
    @Property(bool, notify=statusChanged)
    def busy(self) -> bool:
        return self._status == "working"

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=replyChanged)
    def replyText(self) -> str:
        return self._text

    @Property(str, notify=statusChanged)
    def summary(self) -> str:
        return self.reply.summary if self.reply else ""

    @Property(bool, notify=statusChanged)
    def hasReply(self) -> bool:
        return self._status in ("working", "changed", "answered", "failed")

    @Property(bool, notify=statusChanged)
    def canUndoTurn(self) -> bool:
        r = self.reply
        if r is None or r.turn_txn is None:
            return False
        return self.live.top_id == r.turn_txn

    def _turn_record(self) -> Any:
        """The turn's undo record while it is still applied (not undone, not dropped)."""
        r = self.reply
        if r is None or r.turn_txn is None:
            return None
        applied = self.live.history[: self.live.cursor]
        return next((rec for rec in applied if rec.id == r.turn_txn), None)

    @Property(bool, notify=statusChanged)
    def turnUndone(self) -> bool:
        """The turn changed the canvas and that change is no longer applied (undone)."""
        r = self.reply
        return r is not None and r.status == "changed" and self._turn_record() is None

    @Property(str, notify=statusChanged)
    def turnDiff(self) -> str:
        """The turn's changes as text, one line each, while the turn is still applied
        (empty when it changed nothing or was undone); the strip shows it only when asked."""
        rec = self._turn_record()
        return "\n".join(describe_patch(rec.patch, rec.inverse)) if rec is not None else ""

    @Property(int, notify=statusChanged)
    def attemptsFailed(self) -> int:
        return self.reply.attempts_failed if self.reply else 0

    @Property(str, notify=statusChanged)
    def sessionLabel(self) -> str:
        info = getattr(self.session, "info", None) or {}
        model = info.get("model") or ""
        source = info.get("api_key_source") or ""
        auth = "login" if source in ("", "none") else source
        return f"{model} - {auth}" if model else ""

    # -- the turn's project skills (#161) -------------------------------------------------------
    @Property(str, notify=statusChanged)
    def skillNote(self) -> str:
        """``saved skill specto:<name>`` (or ``deleted``) for the turn's skill changes,
        one entry per skill; empty when the turn changed none."""
        changes = self._skill_changes()
        last = {c.name: c for c in changes}  # a skill saved twice in the turn: once
        return "; ".join(f"{c.action} skill {c.qualified}" for c in last.values())

    def _skill_changes(self) -> list[Any]:
        r = self.reply
        return (r.skills or r.skills_undone) if r is not None else []

    @Property(bool, notify=statusChanged)
    def canUndoSkills(self) -> bool:
        return self.reply is not None and bool(self.reply.skills)

    @Property(bool, notify=statusChanged)
    def skillsUndone(self) -> bool:
        return self.reply is not None and bool(self.reply.skills_undone)

    @Property(str, notify=statusChanged)
    def skillPath(self) -> str:
        changes = self._skill_changes()
        return str(changes[-1].path) if changes else ""

    @Property(str, notify=statusChanged)
    def skillMarkdown(self) -> str:
        """The turn's (last) skill file as markdown for Open: its name and description
        in place of the frontmatter, then the body as written."""
        return self._skill_markdown(self.reply) if self.reply is not None else ""

    @staticmethod
    def _skill_markdown(reply: Reply) -> str:
        changes = reply.skills or reply.skills_undone
        if not changes:
            return ""
        return _skill_file_markdown(changes[-1].qualified, str(changes[-1].path))

    @Slot(result=bool)
    def undoSkills(self) -> bool:
        """The strip's skill Undo: the turn's skill files back as they were, one step."""
        r = self.reply
        if r is None or not r.skills:
            return False
        return self._undo_skills(r)

    def _undo_skills(self, r: Reply) -> bool:
        if not r.skills:
            return False
        if not self.tools.undo_skills(r):
            self.app.notices.warn(
                "a skill changed since this turn: not undone",
                fix="edit it in .specto/skills, or ask the agent",
            )
            return False
        self.statusChanged.emit()
        self._thread.refresh_live()
        return True

    # -- the turn's slash commands and data files (#219) -----------------------------------------
    @Property(str, notify=statusChanged)
    def commandNote(self) -> str:
        """``saved command /<name>`` (or ``deleted``) per command the turn changed, once
        each; empty when it changed none."""
        r = self.reply
        changes = (r.commands or r.commands_undone) if r is not None else []
        last = {c.name: c for c in changes}
        return "; ".join(f"{c.action} command {c.qualified}" for c in last.values())

    @Property(bool, notify=statusChanged)
    def canUndoCommands(self) -> bool:
        return self.reply is not None and bool(self.reply.commands)

    @Property(str, notify=statusChanged)
    def commandMarkdown(self) -> str:
        return self._command_markdown(self.reply) if self.reply is not None else ""

    @staticmethod
    def _command_markdown(reply: Reply) -> str:
        changes = reply.commands or reply.commands_undone
        if not changes:
            return ""
        return _command_file_markdown(changes[-1].qualified, str(changes[-1].path))

    @Slot(result=bool)
    def undoCommands(self) -> bool:
        r = self.reply
        return self._undo_commands(r) if r is not None and r.commands else False

    def _undo_commands(self, r: Reply) -> bool:
        if not r.commands:
            return False
        if not self.tools.undo_commands(r):
            self.app.notices.warn(
                "a command changed since this turn: not undone",
                fix="edit it in .claude/commands, or ask the agent",
            )
            return False
        self.statusChanged.emit()
        self._thread.refresh_live()
        return True

    @Property(str, notify=statusChanged)
    def dataNote(self) -> str:
        """``wrote .specto/layouts/review.json`` (or ``deleted``) per data file the turn
        changed, once each; empty when it changed none."""
        r = self.reply
        changes = (r.data_files or r.data_files_undone) if r is not None else []
        last = {c.rel: c for c in changes}
        return "; ".join(c.note for c in last.values())

    @Property(bool, notify=statusChanged)
    def canUndoData(self) -> bool:
        return self.reply is not None and bool(self.reply.data_files)

    @Property(str, notify=statusChanged)
    def dataMarkdown(self) -> str:
        return self._data_markdown(self.reply) if self.reply is not None else ""

    @staticmethod
    def _data_markdown(reply: Reply) -> str:
        changes = reply.data_files or reply.data_files_undone
        return _data_file_markdown(str(changes[-1].path)) if changes else ""

    @Slot(result=bool)
    def undoData(self) -> bool:
        r = self.reply
        return self._undo_data(r) if r is not None and r.data_files else False

    def _undo_data(self, r: Reply) -> bool:
        if not r.data_files:
            return False
        if not self.tools.undo_data(r):
            self.app.notices.warn(
                "a data file changed since this turn: not undone",
                fix="edit it under .specto, or ask the agent",
            )
            return False
        self.statusChanged.emit()
        self._thread.refresh_live()
        return True

    # -- the turn's link code (#168) -------------------------------------------------------------
    @Property(str, notify=statusChanged)
    def codeNote(self) -> str:
        """``wrote link code <name>: needs your approval`` per file the turn wrote (once
        each); empty when it wrote none."""
        r = self.reply
        changes = (r.code or r.code_undone) if r is not None else []
        last = {c.name: c for c in changes}
        return "; ".join(c.note for c in last.values())

    @Property(bool, notify=statusChanged)
    def canUndoCode(self) -> bool:
        return self.reply is not None and bool(self.reply.code)

    @Property(str, notify=statusChanged)
    def codeMarkdown(self) -> str:
        """The turn's (last) link code file for Open: its path, then the code."""
        return self._code_markdown(self.reply) if self.reply is not None else ""

    @staticmethod
    def _code_markdown(reply: Reply) -> str:
        changes = reply.code or reply.code_undone
        return _code_file_markdown(str(changes[-1].path)) if changes else ""

    @Slot(result=bool)
    def undoCode(self) -> bool:
        """The latest turn's code Undo: its link code files back as they were."""
        r = self.reply
        return self._undo_code(r) if r is not None else False

    def _undo_code(self, r: Reply) -> bool:
        if not r.code:
            return False
        if not self.tools.undo_code(r):
            self.app.notices.warn(
                "a link code file changed since this turn: not undone",
                fix="edit it in .specto/links, or ask the agent",
            )
            return False
        self.statusChanged.emit()
        self._thread.refresh_live()
        return True

    # -- the model chip (#73) ------------------------------------------------------------------
    @Property(list, notify=recipientsChanged)
    def fleetRecipients(self) -> list[str]:
        """The nimitz paths the agent's ``fleet_send`` may message; empty: every fleet.
        With the thread's row per send, it is the user's consent to each send (nimitz's
        own gate is passed with --yes)."""
        return saved_recipients() or []

    @Slot(list, result=bool)
    def setFleetRecipients(self, paths: list[str]) -> bool:
        """Set the allowed recipients (nimitz paths; ``//...`` is refused); an empty list
        returns to every fleet nimitz reports."""
        paths = [str(p).strip() for p in paths if str(p).strip()]
        if not all(PATH_RE.fullmatch(p) for p in paths):
            return False
        save_recipients(paths)
        self.recipientsChanged.emit()
        return True

    @Property(str, notify=modelChanged)
    def model(self) -> str:
        """The model the next turn runs on: ``opus``, ``sonnet`` or ``haiku``."""
        return self._model

    @Property(str, notify=modelChanged)
    def modelLabel(self) -> str:
        return MODEL_LABELS.get(self._model, self._model)

    @Property(list, constant=True)
    def models(self) -> list[dict[str, str]]:
        """The chip's menu: ``{name, label, detail}`` per model."""
        return [{"name": m, "label": MODEL_LABELS[m], "detail": MODEL_DETAILS[m]} for m in MODELS]

    @Slot(str, result=bool)
    def setModel(self, name: str) -> bool:
        """Pick the model for the next turn (a running turn finishes on the old one);
        the live session switches without a restart. Remembered per user."""
        name = str(name).strip().lower()
        if name not in MODELS:
            return False
        if name != self._model:
            self._model = name
            save_model(name)
            if self.session is not None:
                self.session.set_model(name)
            self.modelChanged.emit()
            self.statusChanged.emit()  # sessionLabel
        return True

    # -- the pointer (canvas px) -----------------------------------------------------------------
    @Slot(float, float)
    def setMouse(self, x: float, y: float) -> None:
        self._mouse = (float(x), float(y))

    @Slot()
    def clearMouse(self) -> None:
        self._mouse = None

    def _over(self) -> str | None:
        if self._mouse is None or self.app.controller is None:
            return None
        x, y = self._mouse
        hits = [
            vid
            for vid, (vx, vy, vw, vh) in self.app.controller.rects().items()
            if vx <= x <= vx + vw and vy <= y <= vy + vh
        ]
        focused = self.app.controller.focusedView
        if focused in hits:
            return focused
        return hits[-1] if hits else None

    def _selection(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        links = self.app.engine.links
        for (vid, port), value in links.outputs.items():
            if isinstance(value, Selection):
                out.setdefault(vid, {})[port] = value.model_dump()
            elif port == "selected_files" and isinstance(value, FilesValue) and value.paths:
                out.setdefault(vid, {})[port] = {
                    "count": len(value.paths),
                    "paths": value.paths[:MAX_PATHS],
                }
        return out

    def _files(self) -> dict[str, dict[str, Any]]:
        """The project folder (``project.all_files``) folded to folders and extensions,
        under the key ``project``: always the whole folder from its root (#169), never
        what one view's intaker found; too many folders keep the top levels and point
        at ``list_files``."""
        files = self.tools.files
        return {PROJECT_ID: files.summary()} if files is not None else {}

    def context(self) -> CanvasContext:
        """What this prompt carries: the document when it changed since the agent's last
        turn (its hash otherwise), the viewport, the mouse and the selections."""
        doc = self.live.document()
        h = document_hash(doc)
        viewport = self.app.controller.viewport if self.app.controller else {}
        selected = list(self.app.controller.selectedViews) if self.app.controller else []
        skills_changed = skills_key(self.app.project.root) != self._skills_seen
        mouse = None
        if self._mouse is not None:
            mouse = {"x": self._mouse[0], "y": self._mouse[1], "over": self._over()}
        return CanvasContext(
            document=doc if h != self._last_hash else None,
            viewport=dict(viewport),
            mouse=mouse,
            selection=self._selection(),
            selected_views=selected,
            files=self._files(),
            document_hash=h,
            skills=list_skills(self.app.project.root) if skills_changed else None,
            commands=list_commands(self.app.project.root) if skills_changed else None,
            scratch=context_items(self.app.engine.scratch),
        )

    # -- prompts ------------------------------------------------------------------------------
    @Slot(str, result=bool)
    def submit(self, text: str) -> bool:
        """One prompt: refused while a turn runs, for empty text, and for a slash
        command that is not the project's own (``/init`` and the other built-ins)."""
        text = text.strip()
        if not text:
            return False
        if self.busy:
            self.app.notices.info("the agent is still working on the last request")
            return False
        view = walk_request(text, self.live.data)
        if view and self.walkthrough is not None and getattr(self.factory, "unavailable", ""):
            # no agent: "walk me through <view>" still plays, with template captions
            return bool(self.walkthrough.start(view))
        slash = slash_command(text, self.app.project.root)
        if slash == "":
            name = text.split()[0]
            self.app.notices.warn(
                f"{name} is not one of this project's commands",
                fix="project commands live in .specto/commands and .claude/commands",
            )
            return False
        self._start(text, verbatim=slash is None)
        return True

    def _start(self, text: str, *, verbatim: bool, peer: Entry | None = None) -> None:
        self.reply = None
        self._text = ""
        self._status = "working"
        if peer is None:
            self.peer = None
            self._add("user", text=text)
        else:
            self.peer = {
                "from": peer.sender,
                "reply_to": peer.reply_to or None,
                "id": peer.id,
                "verified": peer.verified,
            }
            self._set_peer(peer, "run")
        sender = peer.sender if peer is not None else ""
        verified = peer.verified if peer is not None else False
        self._agent_entry = self._add(
            "agent", status="working", save=False, sender=sender, verified=verified
        )
        self._icon.turn_started()
        self.replyChanged.emit()
        self.statusChanged.emit()
        loop = self.app.loop or asyncio.get_event_loop()
        self._task = loop.create_task(self._run(text, verbatim))

    async def _run(self, text: str, verbatim: bool) -> None:
        #: the agent got this turn's context: a verbatim turn that ran (a session that
        #: failed to start, or ended mid-turn, may never have sent it: resend it)
        delivered = False
        try:
            if self.session is None:
                self.session = self.factory(self.tools)
                self.session.set_model(self._model)
                await self.session.start()
            reply = await run_turn(
                self.session,
                self.tools,
                text,
                self.context(),
                on_delta=self._delta,
                verbatim=verbatim,
            )
            delivered = verbatim
        except Exception as e:
            msg = e.message if isinstance(e, SpectoError) else f"{type(e).__name__}: {e}"
            reply = Reply(text=msg, summary=msg.splitlines()[0][:200], status="failed", error=msg)
            print(f"[ERROR] prompt bar: {msg}")
        self._finish(reply, context_sent=delivered)

    def _delta(self, piece: str) -> None:
        self._text += piece
        self.replyChanged.emit()
        self._icon.streamed()
        if self._agent_entry is not None:
            self._agent_entry.text = self._text
            self._thread.entry_changed(self._agent_entry)

    def _finish(self, reply: Reply, *, context_sent: bool = True) -> None:
        self.reply = reply
        self.replies.append(reply)
        if context_sent:
            # the agent has the document and the skills now; after a project slash
            # command (the CLI got no context) or a turn that never reached the agent
            # (SPEC-30) the next turn carries them
            self._last_hash = document_hash(self.live.data)
            self._skills_seen = skills_key(self.app.project.root)
        if reply.text and reply.text != self._text:
            self._text = reply.text
            self.replyChanged.emit()
        self._status = reply.status
        self._task = None
        self._thread_rows(reply)
        self._icon.turn_finished(
            reply.status,
            error=reply.error if reply.status == "failed" else "",
            panel_open=self._panel.open,
        )
        self.statusChanged.emit()
        if reply.touched:
            self.touched.emit(list(reply.touched))
        if reply.status == "failed" and reply.error and not reply.text:
            self.app.notices.error(reply.error, command="prompt")
        print(f"[OK] prompt turn {reply.status}: {timing_line(reply)}")
        self.peer = None
        self.turnFinished.emit()
        self._next_peer()

    def _thread_rows(self, reply: Reply) -> None:
        """The finished turn in the thread: the reply (none when the turn changed the
        canvas and its label is all it said, #98), its turn row, its skill and code
        rows, a row per launch (#186)."""
        entry, self._agent_entry = self._agent_entry, None
        if entry is not None:
            text = reply.text
            if reply.status == "changed" and text == reply.summary:
                self._thread.removed(entry)
            else:
                if not text and reply.status == "failed":
                    text = reply.error or reply.summary
                self._thread.thread.update(entry, text=text, status=reply.status, save=True)
                self._thread.entry_changed(entry)
        rec = self._turn_record()
        if rec is not None:
            row = self._add(
                "turn",
                label=reply.summary,
                counts=patch_counts(rec.patch),
                diff=self.turnDiff,
                sender=self.peer["from"] if self.peer else "",
                verified=bool(self.peer and self.peer.get("verified")),
            )
            self._rows[row.id] = (reply, reply.turn_txn)
        if reply.skills:
            row = self._add("skill", label=self.skillNote, path=str(reply.skills[-1].path))
            self._rows[row.id] = (reply, None)
        if reply.commands:
            row = self._add("command", label=self.commandNote, path=str(reply.commands[-1].path))
            self._rows[row.id] = (reply, None)
        if reply.data_files:
            row = self._add("data", label=self.dataNote, path=str(reply.data_files[-1].path))
            self._rows[row.id] = (reply, None)
        if reply.code:
            row = self._add("code", label=self.codeNote, path=str(reply.code[-1].path))
            self._rows[row.id] = (reply, None)
        if reply.scratch:
            row = self._add("scratch", label=f"scratch pad: {turn_label(reply.scratch)}")
            self._rows[row.id] = (reply, None)
        for send in reply.fleet:
            label = f"to {send.to}" if send.sent else f"not sent to {send.to}: {send.detail}"
            if send.mode:
                label += " (sender via --from)" if send.mode == "from" else " (sender in the text)"
            status = "sent" if send.sent else "failed"
            self._add("fleet", label=label, text=send.text, status=status)
        for done in reply.launches:
            self._add("launch", label=done.note, status="failed" if done.refused else "")
        self._thread.refresh_live()

    @Slot(result=bool)
    def undoTurn(self) -> bool:
        """The latest turn's Undo: the whole turn, one step, as the user."""
        if self.reply is None or not self.canUndoTurn:
            return False
        return self._undo_turn(self.reply)

    @Slot(str)
    def prefill(self, text: str) -> None:
        """Put ``text`` in the field and focus it ("Ask agent to change" on a link, #45)."""
        self._panel.summon()
        self.prefillRequested.emit(str(text))

    @Property(str, notify=draftChanged)
    def draft(self) -> str:
        """The field's current text (the QML field keeps it current)."""
        return self._draft

    @Slot(str)
    def setDraft(self, text: str) -> None:
        if text != self._draft:
            self._draft = text
            self.draftChanged.emit()

    @Slot()
    def interrupt(self) -> None:
        if self.session is not None and self.busy:
            loop = self.app.loop or asyncio.get_event_loop()
            loop.create_task(self.session.interrupt())

    @Slot()
    def dismiss(self) -> None:
        if not self.busy:
            self._status = "idle"
            self.statusChanged.emit()

    # -- peer requests (#187) ---------------------------------------------------------------------
    @Property(str, notify=peerRequestsChanged)
    def peerRequests(self) -> str:
        """What a peer's request does on arrival: ``auto``, ``ask`` or ``off``."""
        return self._peer_mode

    @Property(list, constant=True)
    def peerModes(self) -> list[dict[str, str]]:
        """The setting's menu: ``{name, detail}`` per mode."""
        return [{"name": m, "detail": PEER_MODE_DETAILS[m]} for m in PEER_MODES]

    @Slot(str, result=bool)
    def setPeerRequests(self, mode: str) -> bool:
        mode = str(mode).strip().lower()
        if mode not in PEER_MODES:
            return False
        if mode != self._peer_mode:
            self._peer_mode = mode
            save_peer_mode(mode)
            self.peerRequestsChanged.emit()
        return True

    def receive_peer(
        self,
        sender: str,
        text: str,
        reply_to: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """A peer's message (the endpoint's ``message`` command): a peer row in the
        thread, then a turn now, a turn after the running one, or (ask) the user's call.
        Refused when the setting is off: the sender gets the error, and the thread keeps
        a refused row (the sender, the request's first line, why; #204) so the user sees
        what was turned away. ``labels`` are the caller's nimitz member labels: the sender
        is verified when they name it, else only claimed (#190); either way the setting
        applies and nothing is approved."""
        sender, text, reply_to = check_message(sender, text, reply_to)
        verified = sender_verified(sender, labels)
        mode = self._peer_mode
        if mode == "off":
            why = "Peer requests is off"
            self._add(
                "peer",
                text=text.splitlines()[0],
                sender=sender,
                reply_to=reply_to,
                verified=verified,
                status="refused",
                label=why,
            )
            print(f"[OK] peer request from {sender}: refused ({why})")
            raise PeerRefused(
                f"{self.instance_name or 'this Specto'} does not take peer requests",
                fix="the user can set Peer requests to auto or ask in the agent panel",
            )
        out = {"mode": mode, "instance": self.instance_name, "verified": verified}
        row = {"text": text, "sender": sender, "reply_to": reply_to, "verified": verified}
        if mode == "ask":
            entry = self._add("peer", **row, status="asked")
            self._asked[entry.id] = entry
            self._icon.peer_asked()
            self._thread.refresh_live()
            print(f"[OK] peer request from {sender}: waiting for the user")
            return {**out, "id": entry.id, "status": "waiting"}
        status = "queued" if self.busy or self._queue else "run"
        entry = self._add("peer", **row, status=status)
        print(f"[OK] peer request from {sender}: {status}")
        if status == "queued":
            self._queue.append(entry)
            return {**out, "id": entry.id, "status": "queued"}
        self._start_peer(entry)
        return {**out, "id": entry.id, "status": "running"}

    @Slot(int, result=bool)
    def runPeer(self, entry_id: int) -> bool:
        """An asked peer row's Run: the request runs as a turn (after the running one)."""
        entry = self._asked.pop(int(entry_id), None)
        if entry is None:
            return False
        if self.busy or self._queue:
            self._queue.append(entry)
            self._set_peer(entry, "queued")
        else:
            self._start_peer(entry)
        self._thread.refresh_live()
        return True

    @Slot(int, result=bool)
    def dismissPeer(self, entry_id: int) -> bool:
        """An asked peer row's Dismiss: it stays in the thread, marked dismissed."""
        entry = self._asked.pop(int(entry_id), None)
        if entry is None:
            return False
        self._set_peer(entry, "dismissed")
        self._thread.refresh_live()
        return True

    def _start_peer(self, entry: Entry) -> None:
        prompt = peer_prompt(entry.sender, entry.text, entry.reply_to, entry.verified)
        self._start(prompt, verbatim=True, peer=entry)

    def _set_peer(self, entry: Entry, status: str) -> None:
        self._thread.thread.update(entry, status=status, save=True)
        self._thread.entry_changed(entry)

    def _next_peer(self) -> None:
        """The oldest queued peer request, once no turn runs."""
        if self._queue and not self.busy and self.app is not None:
            self._start_peer(self._queue.pop(0))

    # -- approval requests (#203) -----------------------------------------------------------------
    def ask_approval(
        self,
        link: str,
        path: str,
        hash: str,
        target: dict[str, Any],
        requester: str,
        verified: bool,
    ) -> int:
        """A ``link.approve`` request from the endpoint (``engine.host.approvals``): an
        approval card in the thread, the icon's badge, and the card's id back to the
        caller. Nothing is approved here: the user's click on the card (or the view's
        header) is the approval."""
        view = str((target or {}).get("view", ""))
        entry = self._add(
            "approval",
            text=f"{path} for {link} -> {view}",
            label=f"approve {path}",
            path=str(self.app.project.root / path),
            link=link,
            hash=hash,
            sender=requester,
            verified=verified,
            status="asked",
        )
        self._approvals[entry.id] = entry
        self._icon.peer_asked()
        self._thread.refresh_live()
        print(f"[OK] approval request for {link} ({path}) from {requester}: waiting for the user")
        return entry.id

    @Slot(int, result=bool)
    def approveLink(self, entry_id: int) -> bool:
        """The card's Approve: the user approves the file as it is now (the same gesture
        as the view's Approve). Refused, the card marked changed, when the file no longer
        holds what the requester asked about: the user reads the new content (Open) and
        approves it in the view's header, or the requester asks again."""
        from specto_core.python_links import code_hash

        entry = self._approvals.get(int(entry_id))
        if entry is None:
            return False
        links = self.app.engine.links
        now = code_hash(
            links.root,
            (links.live.data["links"].get(entry.link) or {}).get("python", {}).get("path", ""),
        )
        if now != entry.hash:
            self._approvals.pop(entry.id, None)
            self._set_approval(entry, "changed")
            self.app.notices.warn(
                f"{entry.link}: the file changed since {entry.sender} asked; not approved",
                fix="read it (Open) and approve it in the view's header, or ask again",
            )
            return False
        controller = self.app.controller
        ok = controller.gesture(lambda: links.approve(entry.link), "approve")
        if not ok:
            return False
        self._approvals.pop(entry.id, None)
        self._set_approval(entry, "approved")
        self.app.notices.info(f"approved {entry.link}")
        controller.rebuild()
        return True

    @Slot(int, result=bool)
    def dismissApproval(self, entry_id: int) -> bool:
        """The card's Dismiss: it stays in the thread, marked dismissed; the link keeps
        waiting (needs_approval)."""
        entry = self._approvals.pop(int(entry_id), None)
        if entry is None:
            return False
        self._set_approval(entry, "dismissed")
        return True

    def _set_approval(self, entry: Entry, status: str) -> None:
        self._thread.thread.update(entry, status=status, save=True)
        self._thread.entry_changed(entry)
        self._thread.refresh_live()

    def _link_event(self, ev: Any) -> None:
        """A card's link resolving (the user approved the file in the view's header):
        the card closes as approved."""
        if not self._approvals or ev.kind not in ("link.resolving", "link.resolved"):
            return
        lid = (ev.payload or {}).get("link")
        for entry in [e for e in self._approvals.values() if e.link == lid]:
            self._approvals.pop(entry.id, None)
            self._set_approval(entry, "approved")

    # -- lifecycle ----------------------------------------------------------------------------
    async def aclose(self) -> None:
        self._off_patch()
        if self._task is not None:
            self._task.cancel()
        if self.session is not None:
            session, self.session = self.session, None
            await session.close()

    def close(self) -> None:
        """Sync close for the app's ``close()``: finishes the session when the loop is
        not running (tests); the app's ``run()`` does it after the loop returned."""
        loop = self.app.loop
        if loop is None or loop.is_closed() or loop.is_running():
            return
        try:
            loop.run_until_complete(asyncio.wait_for(self.aclose(), 10))
        except Exception as e:
            print(f"[WARN] agent session close: {type(e).__name__}: {e}")

    def release(self) -> None:
        """Drop what points back at the app (the tools' and the factory's closures, the
        patch listener) once the QML that binds to the bar is gone, so a closed app is
        freed by reference count and not by the cyclic GC inside some later Qt loop."""
        self._off_patch()
        self._off_scratch()
        self._off_events()
        self._icon.stop()
        self._panel.stop()
        if self.tools is not None and self.tools.files is not None:
            self.tools.files.close()
        self.session = None
        self.walkthrough = None
        self.tools = self.factory = self.app = None  # type: ignore[assignment]


def _skill_file_markdown(qualified: str, path: str) -> str:
    """A skill file for Open: its name and description in place of the frontmatter,
    then the body as written."""
    head = f"**{qualified}**\n\n"
    try:
        meta, body = parse_frontmatter(Path(path).read_text(encoding="utf-8"))
    except (OSError, SpectoError):
        return head + "*(the file is gone)*"
    if meta.get("description"):
        head += f"*{meta['description']}*\n\n"
    return head + body


def _command_file_markdown(qualified: str, path: str) -> str:
    """A command file for Open (#219): its name, description and argument hint in place
    of the frontmatter, then the body as written."""
    head = f"**{qualified}**\n\n"
    try:
        meta, body = parse_frontmatter(Path(path).read_text(encoding="utf-8"))
    except (OSError, SpectoError):
        return head + "*(the file is gone)*"
    if meta.get("description"):
        head += f"*{meta['description']}*\n\n"
    if meta.get("argument-hint"):
        head += f"arguments: `{meta['argument-hint']}`\n\n"
    return head + body


def _data_file_markdown(path: str) -> str:
    """A data file for Open (#219): its project path, then the text (a .md as it is,
    the rest in a fenced block of its kind)."""
    p = Path(path)
    posix = p.as_posix()
    at = posix.rfind("/.specto/")
    head = f"**{posix[at + 1 :] if at >= 0 else p.name}**\n\n"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return head + "*(the file is gone)*"
    if p.suffix.lower() == ".md":
        return head + text
    lang = p.suffix.lstrip(".").lower()
    fence = "````" if "```" in text else "```"
    return f"{head}{fence}{lang}\n{text.rstrip()}\n{fence}\n"


def _to_png(data: bytes) -> bytes | None:
    from ..views.scratch_view import to_png

    return to_png(data)


def _code_file_markdown(path: str) -> str:
    """A link code file for Open: its project path, then the code in a fenced block."""
    p = Path(path)
    head = f"**.specto/links/{p.name}**\n\n"
    try:
        code = p.read_text(encoding="utf-8")
    except OSError:
        return head + "*(the file is gone)*"
    fence = "````" if "```" in code else "```"
    return f"{head}{fence}python\n{code.rstrip()}\n{fence}\n"


def _description(root: Path, kind: str, name: str) -> str:
    """A skill's or command's one line for the ``/`` completion: its frontmatter's
    description, else its first line."""
    plugin, _, short = name.rpartition(":")
    base = root / (".specto" if plugin == SERVER_NAME else ".claude")
    if kind == "skill":
        path = base / "skills" / short / "SKILL.md"
    else:
        path = base / "commands" / f"{short}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        meta, body = parse_frontmatter(text)
    except SpectoError:
        meta, body = {}, text  # a command file needs no frontmatter
    desc = str(meta.get("description") or "").strip()
    if not desc:
        desc = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip()), "")
    return desc.splitlines()[0][:120] if desc else ""


__all__ = ["PromptBar", "SessionFactory", "save_model", "saved_model"]
