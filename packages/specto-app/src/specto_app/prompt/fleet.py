"""The prompt-bar agent's outbound fleet tools (#187 section 4): ``fleet_status`` and
``fleet_send`` over the nimitz CLI (an argv list, never a shell).

``fleet_status {path?}`` runs ``nimitz status [path] --json`` and answers each member's
path, role, status word, topic and whether it is live, with the recipients
``fleet_send`` may use. ``fleet_send {to, text, reply_to?}`` runs ``nimitz signal <to>
--inbox [--from specto:<instance>] --yes <text>``: the message lands in the member's
inbox as a request, never as the user's typed input. ``--yes`` passes nimitz's own plan
gate (it refuses a non-terminal otherwise); the consent is the user's: the recipients
setting, and one thread row per send.

Sender: nimitz names a non-member sender only through ``signal --from`` (``SENDER_FLAG``,
nimitz 0.7.13; older ones have none and relay the message "from a `nimitz signal` run with
no member labels", which is true). ``fleet_send`` asks ``nimitz help signal`` once: with
the flag, nimitz relays it as claimed by ``specto:<instance>`` (``sender_label``: short,
plain, never a member id or "user"); without it, or when nimitz refuses the label (mode
``declared``), the text itself starts with a plainly self-declared line, "Specto instance
<name> (project <path>) says:". Either way it ends with the reply hint "reply: specto
message <name> <text>". Specto never sets NIMITZ_* variables to pass for a member (#190).

Recipients: a user setting (``RECIPIENTS_SETTING``, a list of nimitz paths: ``FLEET/...``,
``FLEET/SQUAD/...`` or one member ``FLEET/SQUAD/SHIP``); unset, every fleet nimitz
reports (``F/...`` each). ``//...`` is never a recipient, whatever the setting says.
Every send, sent or refused, comes back as a ``FleetSend`` for the thread's row.

The executable (:func:`find_nimitz`): ``SPECTO_NIMITZ``, else ``nimitz`` on PATH, else the
folders nimitz installs to (``INSTALL_DIRS``): a window opened from the Dock, Finder or
launchd gets the session's bare PATH, without ``~/.cargo/bin``. PATH comes before those
folders so a nimitz the user put first (or a test's fake) wins. The
nimitz runs block (a status takes about 0.5 s): ``TurnTools.acall`` runs these tools on a
worker thread. No Qt here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from specto_core.agent_tools import ToolSpec
from specto_core.errors import InvalidArgs, NotFound, SpectoError

#: the QSettings key of the allowed recipients (a JSON list of nimitz paths)
RECIPIENTS_SETTING = "agent/fleet_recipients"
#: the ``nimitz signal`` flag that names a non-member sender
SENDER_FLAG = "--from"
#: the nimitz executable when not ``nimitz`` on PATH (the seam-3 flow's fake nimitz)
EXE_VAR = "SPECTO_NIMITZ"
#: where nimitz is installed when PATH lacks it (a GUI launch): cargo install, uv tool /
#: pipx, Homebrew (Apple silicon), the usual prefix; ``~`` is the user's home
INSTALL_DIRS = ("~/.cargo/bin", "~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")
STATUS_TIMEOUT_S = 20.0
SEND_TIMEOUT_S = 30.0
MAX_TEXT = 4000

_NAME = r"[A-Z0-9_]+"
#: a nimitz path: one to three names (a member), or one or two and ``/...`` (under it)
PATH_RE = re.compile(rf"{_NAME}(?:/{_NAME}){{0,2}}|{_NAME}(?:/{_NAME})?/\.\.\.")

FLEET_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "fleet_status",
            "The user's nimitz fleets (other Claude Code agents the user runs): each "
            "member's path (FLEET is a fleet's admiral, FLEET/SQUAD a squadron's commodore, "
            "FLEET/SQUAD/SHIP a ship), role, status, topic and whether it is live, and "
            "the recipients fleet_send may message.",
            read_only=True,
            params={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Only these members: one member (FLEET/SQUAD) or "
                        "everything under one (FLEET/...). Default: every fleet.",
                    }
                },
            },
        ),
        ToolSpec(
            "fleet_send",
            "Message a member of the user's nimitz fleet: it lands in the member's inbox "
            "as a request from this Specto window (specto:<its name>), never as the user's "
            "input or approval. to is one member (FLEET/SQUAD/SHIP), or F/... or "
            "F/SQ/... for every live member under it; only the recipients the user allows "
            "(fleet_status lists them), never //... . To answer a peer request, send to "
            "its sender and pass its reply_to. One short message; the user sees every send "
            "in the thread.",
            params={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "A nimitz path: FLEET/SQUAD/SHIP, FLEET/SQUAD/... ",
                    },
                    "text": {"type": "string", "description": "The message, one line."},
                    "reply_to": {
                        "type": "string",
                        "description": "The reply_to of the peer request this answers.",
                    },
                },
                "required": ["to", "text"],
            },
        ),
    )
}


@dataclass
class FleetSend:
    """One ``fleet_send`` for the thread: whom, what, and whether nimitz took it."""

    to: str
    text: str
    sent: bool
    #: why it was not sent (``sent`` False), or nimitz's own line when it was
    detail: str = ""
    #: how the sender was named: ``from`` (nimitz's --from) or ``declared`` (in the text)
    mode: str = ""


Run = Callable[..., subprocess.CompletedProcess]


def find_nimitz(
    exe: str | None = None, environ: dict[str, str] | None = None
) -> tuple[str | None, list[str]]:
    """The nimitz to run and where it looked: ``exe`` (or ``SPECTO_NIMITZ``; a path, or a
    name looked up on PATH), else ``nimitz`` on PATH, else the first of ``INSTALL_DIRS``
    that has it. None when nothing has it."""
    env = os.environ if environ is None else environ
    path = env.get("PATH") or None

    def on(name: str, folders: str | None) -> str | None:
        return shutil.which(name, path=folders) if folders else None

    asked = exe or env.get(EXE_VAR)
    if asked:
        found = on(asked, path) or (asked if os.path.isfile(asked) else None)
        return found, [asked]
    looked = ["PATH"]
    found = on("nimitz", path)
    for folder in INSTALL_DIRS:
        if found:
            break
        folder = os.path.expanduser(folder)
        looked.append(folder)
        found = on("nimitz", folder)
    return found, looked


class Nimitz:
    """The nimitz CLI, run with an argv list (no shell), text out."""

    def __init__(self, exe: str | None = None, *, run: Run = subprocess.run) -> None:
        self.exe = exe  # None: find_nimitz at each run (SPECTO_NIMITZ, PATH, INSTALL_DIRS)
        self.run_fn = run
        self._sender_flag: bool | None = None

    def _run(self, args: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
        exe, looked = find_nimitz(self.exe)
        if exe is None:
            raise NotFound(
                f"nimitz is not installed where Specto looks ({', '.join(looked)})",
                fix=f"install claude-nimitz, or set {EXE_VAR} to its path, or tell the user",
            )
        try:
            return self.run_fn(
                [exe, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            raise NotFound(
                f"nimitz could not be started ({exe})",
                fix=f"install claude-nimitz, or set {EXE_VAR} to its path, or tell the user",
            ) from None
        except subprocess.TimeoutExpired:
            raise SpectoError(f"nimitz {args[0]} took longer than {timeout:.0f} s") from None

    def status(self, path: str | None = None) -> dict[str, Any]:
        p = self._run(["status", *([path] if path else []), "--json"], STATUS_TIMEOUT_S)
        if p.returncode != 0:
            raise SpectoError(f"nimitz status failed: {_tail(p)}")
        try:
            data = json.loads(p.stdout)
        except ValueError:
            raise SpectoError("nimitz status --json printed no JSON") from None
        if not isinstance(data, dict):
            raise SpectoError("nimitz status --json printed no JSON object")
        return data

    def names_sender(self) -> bool:
        """Whether ``nimitz signal`` takes ``--from`` (asked once per window)."""
        if self._sender_flag is None:
            p = self._run(["help", "signal"], STATUS_TIMEOUT_S)
            self._sender_flag = SENDER_FLAG in (p.stdout or "")
        return self._sender_flag

    def without_sender(self) -> None:
        """This nimitz refused ``--from``: send with the body's sender line from now on."""
        self._sender_flag = False

    def signal(self, to: str, text: str, sender: str | None) -> subprocess.CompletedProcess:
        """``sender`` goes as ``--from``; ``None`` when this nimitz has no such flag. A
        text starting with ``-`` gets a space first, so nimitz never reads it as a flag."""
        named = [SENDER_FLAG, sender] if sender else []
        text = f" {text}" if text.startswith("-") else text
        return self._run(["signal", to, "--inbox", *named, "--yes", text], SEND_TIMEOUT_S)


#: what a ``--from`` label keeps of an instance name (nimitz: a short plain label)
_LABEL_CHARS = re.compile(r"[^a-z0-9_-]")
MAX_LABEL_NAME = 24


def sender_label(name: str) -> str | None:
    """The ``--from`` label for instance ``name``: ``specto:<name>``, lower-case letters,
    digits, ``_`` and ``-`` only, at most ``MAX_LABEL_NAME`` of them; no control
    characters or newlines, no ``/`` or brackets (never a member id), never "user".
    ``None`` when nothing of the name is left."""
    plain = _LABEL_CHARS.sub("", str(name or "").lower())[:MAX_LABEL_NAME]
    return f"specto:{plain}" if plain else None


def members(status: dict[str, Any]) -> list[dict[str, Any]]:
    """Every member of ``nimitz status --json`` in config order, briefly."""
    out = []
    for fleet in status.get("fleets") or []:
        found = [fleet.get("admiral")]
        for sq in fleet.get("squadrons") or []:
            found += [sq.get("commodore"), *(sq.get("ships") or [])]
        for m in found:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            session = m.get("session") or {}
            out.append(
                {
                    "id": m["id"],
                    "role": m.get("role", ""),
                    "status": m.get("status", ""),
                    "live": session.get("pid") is not None,
                    "topic": m.get("topic") or "",
                }
            )
    return out


def default_recipients(status: dict[str, Any]) -> list[str]:
    """The user's fleets, ``F/...`` each: every member of every fleet nimitz reports."""
    return [f"{f['id']}/..." for f in status.get("fleets") or [] if f.get("id")]


def covers(allowed: str, path: str) -> bool:
    """Whether the recipient entry ``allowed`` covers the path ``path`` (both nimitz
    paths): ``F/...`` covers F and everything under it, an exact path only itself."""
    if allowed.endswith("/..."):
        head = allowed.removesuffix("/...")
        base = path.removesuffix("/...")
        return base == head or base.startswith(head + "/")
    return path == allowed


class FleetTools:
    """``fleet_status`` and ``fleet_send`` for one window. ``instance()`` is the
    window's instance name (rasel), ``project`` its project folder; ``recipients()`` the
    user's setting, ``None`` when unset (then every fleet nimitz reports); ``peer()`` the
    peer request the turn runs (``PromptBar.peer``: ``{from, reply_to, id}``) or ``None``,
    so an answer to its sender quotes its ``reply_to`` by default."""

    def __init__(
        self,
        nimitz: Nimitz,
        *,
        instance: Callable[[], str],
        project: str = "",
        recipients: Callable[[], list[str] | None] = lambda: None,
        peer: Callable[[], dict[str, Any] | None] = lambda: None,
    ) -> None:
        self.nimitz = nimitz
        self.instance = instance
        self.project = str(project)
        self.recipients = recipients
        self.peer = peer

    def allowed(self, status: dict[str, Any] | None = None) -> list[str]:
        chosen = self.recipients()
        if chosen is None:
            chosen = default_recipients(status if status is not None else self.nimitz.status())
        return [p for p in chosen if PATH_RE.fullmatch(p)]  # never //...

    def run(self, tool: str, args: dict[str, Any]) -> tuple[FleetSend | None, dict[str, Any]]:
        if tool == "fleet_status":
            return None, self.status(args.get("path"))
        if tool == "fleet_send":
            return self.send(args.get("to"), args.get("text"), args.get("reply_to"))
        raise KeyError(tool)

    def status(self, path: str | None = None) -> dict[str, Any]:
        if path and not PATH_RE.fullmatch(path):
            raise InvalidArgs(f"{path!r} is not a nimitz path", fix=_PATH_FIX)
        data = self.nimitz.status(path or None)
        return {
            "machine": data.get("machine", ""),
            "members": members(data),
            "recipients": self.allowed(None if path else data),
            "issues": len(data.get("issues") or []),
        }

    def send(self, to: Any, text: Any, reply_to: Any = None) -> tuple[FleetSend, dict[str, Any]]:
        to, text = str(to or "").strip(), " ".join(str(text or "").split())
        if not to or not text:
            raise InvalidArgs("fleet_send takes to and text", fix=_PATH_FIX)
        if len(text) > MAX_TEXT:
            raise InvalidArgs(f"the message is over {MAX_TEXT} characters", fix="shorten it")
        if not PATH_RE.fullmatch(to):
            raise InvalidArgs(f"{to!r} is not a nimitz path (never //...)", fix=_PATH_FIX)
        peer = self.peer() or {}
        if not reply_to and peer.get("from") == to:
            reply_to = peer.get("reply_to")
        if reply_to:
            text = f"[reply_to: {reply_to}] {text}"
        allowed = self.allowed()
        if not any(covers(a, to) for a in allowed):
            raise SendRefused(
                FleetSend(to, text, False, "not among the recipients the user allows"),
                f"{to} is not among the recipients the user allows; nothing was sent",
                fix=f"allowed: {', '.join(allowed) or 'none'}; ask the user to add it",
            )
        name = self.instance()
        label = sender_label(name)
        hint = f" -- reply: specto message {name} <text>"
        declared = f"Specto instance {name} (project {self.project}) says: {text}{hint}"
        data: dict[str, Any] = {}
        if label is not None and self.nimitz.names_sender():
            mode, body = "from", f"{text}{hint}"
            p = self.nimitz.signal(to, body, label)
            if p.returncode != 0 and SENDER_FLAG in f"{p.stdout}{p.stderr}":
                # this nimitz refused the label or the flag: say who we are in the body
                data["from_refused"] = _tail(p)
                self.nimitz.without_sender()
                mode, body = "declared", declared
                p = self.nimitz.signal(to, body, None)
        else:
            mode, body = "declared", declared
            p = self.nimitz.signal(to, body, None)
        if p.returncode != 0:
            why = _tail(p)
            raise SendRefused(FleetSend(to, body, False, why, mode), f"nimitz refused: {why}")
        line = _last_line(p.stdout)
        sender = label if mode == "from" else f"specto:{name} (self-declared in the text)"
        data.update(sent=True, to=to, sender=sender, named_by=mode, nimitz=line)
        return FleetSend(to, body, True, line, mode), data


class SendRefused(SpectoError):
    """A ``fleet_send`` that sent nothing; ``send`` is its thread row."""

    code = "refused"

    def __init__(self, send: FleetSend, message: str, fix: str | None = None) -> None:
        super().__init__(message, fix=fix)
        self.send = send


_PATH_FIX = "to is a nimitz path such as FLEET/SQUAD/SHIP or FLEET/SQUAD/... (fleet_status)"


def _last_line(text: str | None) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _tail(p: subprocess.CompletedProcess) -> str:
    """Why a nimitz run failed: its ``[ERROR]`` line (nimitz prints it on stdout and a
    usage hint on stderr), else the last line of either."""
    lines = f"{p.stdout or ''}\n{p.stderr or ''}".splitlines()
    error = next((ln.strip() for ln in lines if ln.strip().startswith("[ERROR]")), "")
    return error or _last_line(p.stderr) or _last_line(p.stdout) or f"exit {p.returncode}"


__all__ = [
    "FLEET_TOOLS",
    "RECIPIENTS_SETTING",
    "FleetSend",
    "FleetTools",
    "Nimitz",
    "SendRefused",
    "covers",
    "default_recipients",
    "members",
    "sender_label",
]
