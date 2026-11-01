"""Slash commands the prompt-bar agent writes for its own project (#219):
``save_command`` and ``delete_command`` over ``<project>/.claude/commands/<name>.md``,
which the session loads as the project command ``/<name>`` (``session.project_commands``;
the prompt bar lets it through ``slash_command`` and the CLI expands it). A command
added mid-session loads like a skill: the session reconnects with resume after the turn
(``skills.skills_key`` covers both).

The frontmatter is Specto's, not the agent's: ``description`` and ``argument-hint``
only, so a command can give the agent a request to run but never permissions (no
``allowed-tools``, no ``model``; a body line that would run a shell command before the
request, ``!`...```, is refused too). Each call returns a ``CommandChange`` carrying
the file before and after, so the thread's command row undoes a turn's command changes
in one step. No Qt here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from specto_core.agent_tools import ToolSpec
from specto_core.errors import InvalidArgs, NotFound

from .skills import MAX_BODY, MAX_DESCRIPTION, MAX_NAME, NAME_RE, parse_frontmatter

COMMANDS_DIR = ".claude/commands"
MAX_HINT = 200
#: a line that Claude Code would run as a shell command before the request
SHELL_LINE_RE = re.compile(r"^\s*!`", re.MULTILINE)

COMMAND_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "save_command",
            "Save a slash command for this project as .claude/commands/<name>.md: the user "
            "runs it from the prompt bar as /<name> [arguments], and its body reaches you "
            "as their request with $ARGUMENTS (or $1, $2, ...) standing for what they "
            "typed after the name. Usable from the user's next message. Saving an "
            "existing name replaces that command. The body may tell you to invoke a "
            "skill; it cannot grant tools or run shell commands.",
            params={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "kebab-case, at most 64 characters: layout",
                    },
                    "description": {
                        "type": "string",
                        "description": "One line: what the command does (the user sees it in "
                        "the / completion).",
                    },
                    "body": {
                        "type": "string",
                        "description": "The request the command sends you, in markdown (no "
                        "frontmatter); $ARGUMENTS is what the user typed after /<name>.",
                    },
                    "argument_hint": {
                        "type": "string",
                        "description": "What the arguments are, shown in the completion: "
                        "<name> | save <name>",
                    },
                },
                "required": ["name", "description", "body"],
            },
        ),
        ToolSpec(
            "delete_command",
            "Delete one of this project's slash commands (/<name>) by its name.",
            destructive=True,
            params={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "the command's name"}},
                "required": ["name"],
            },
        ),
    )
}


@dataclass(frozen=True)
class CommandChange:
    """One command file written or removed: its text before (``None``: it was new) and
    after (``None``: deleted)."""

    name: str
    path: Path
    before: str | None
    after: str | None

    @property
    def qualified(self) -> str:
        return f"/{self.name}"

    @property
    def action(self) -> str:
        return "deleted" if self.after is None else "saved"


def command_text(description: str, body: str, argument_hint: str = "") -> str:
    desc = json.dumps(description, ensure_ascii=False)
    hint = ""
    if argument_hint:
        hint = f"argument-hint: {json.dumps(argument_hint, ensure_ascii=False)}\n"
    return f"---\ndescription: {desc}\n{hint}---\n\n{body}"


def list_commands(root: Path) -> list[dict[str, str]]:
    """``[{name, description}]`` of every project command (``.claude/commands`` as
    ``/<name>``, ``.specto/commands`` as ``/specto:<name>``), sorted by name."""
    out: list[dict[str, str]] = []
    for where, prefix in ((".claude", "/"), (".specto", "/specto:")):
        for path in sorted((Path(root) / where / "commands").glob("*.md")):
            try:
                meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, InvalidArgs):
                meta = {}
            out.append({"name": prefix + path.stem, "description": meta.get("description", "")})
    return sorted(out, key=lambda c: c["name"])


class CommandStore:
    """The project's ``.claude/commands``: validated writes, deletes and their undo."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dir = self.root / COMMANDS_DIR

    def path(self, name: str) -> Path:
        return self.dir / f"{name}.md"

    def _read(self, name: str) -> str | None:
        try:
            return self.path(name).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def _check_name(self, name: str) -> None:
        if len(name) > MAX_NAME:
            raise InvalidArgs(f"command name {name[:20]!r}... is longer than {MAX_NAME} characters")
        if not NAME_RE.match(name):
            raise InvalidArgs(
                f"command name {name!r} is not kebab-case",
                fix="lowercase letters and digits joined by single hyphens: layout",
            )

    def save(
        self, name: str, description: str, body: str, argument_hint: str = ""
    ) -> CommandChange:
        name = str(name).strip().lstrip("/")
        description = str(description).strip()
        hint = str(argument_hint or "").strip()
        self._check_name(name)
        if not description:
            raise InvalidArgs("the command needs a description", fix="one line: what it does")
        if "\n" in description or "\r" in description:
            raise InvalidArgs("the description must be one line")
        if len(description) > MAX_DESCRIPTION:
            raise InvalidArgs(f"the description is longer than {MAX_DESCRIPTION} characters")
        if "\n" in hint or "\r" in hint or len(hint) > MAX_HINT:
            raise InvalidArgs(f"the argument hint must be one line, at most {MAX_HINT} characters")
        body = str(body).replace("\r\n", "\n")
        if not body.strip():
            raise InvalidArgs("the command's body is empty", fix="write the request it sends")
        if len(body) > MAX_BODY:
            raise InvalidArgs(f"the command's body is longer than {MAX_BODY} characters")
        if body.lstrip().startswith("---"):
            raise InvalidArgs(
                "the body starts with its own frontmatter",
                fix="pass the request alone; Specto writes the frontmatter (description and "
                "argument-hint only: a command cannot declare allowed-tools or model)",
            )
        if SHELL_LINE_RE.search(body):
            raise InvalidArgs(
                "the body has a line starting with !` (a shell command run before the request)",
                fix="a command only writes a request for you; drop the shell line",
            )
        if not body.endswith("\n"):
            body += "\n"
        text = command_text(description, body, hint)
        meta, _ = parse_frontmatter(text)
        want = {"description": description, **({"argument-hint": hint} if hint else {})}
        if meta != want:
            raise InvalidArgs("the command's frontmatter does not parse back")
        before = self._read(name)
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return CommandChange(name, path, before, text)

    def delete(self, name: str) -> CommandChange:
        name = str(name).strip().lstrip("/")
        self._check_name(name)
        before = self._read(name)
        if before is None:
            have = ", ".join(p.stem for p in sorted(self.dir.glob("*.md")))
            raise NotFound(f"no command /{name} in this project", fix=f"commands: {have or 'none'}")
        self.path(name).unlink()
        return CommandChange(name, self.path(name), before, None)

    def undo(self, changes: list[CommandChange]) -> bool:
        """Put every changed command back as it was before ``changes`` (one turn's).
        Refused, changing nothing, when a file no longer holds what the turn left
        there (edited since)."""
        last: dict[str, CommandChange] = {}
        first: dict[str, CommandChange] = {}
        for c in changes:
            first.setdefault(c.name, c)
            last[c.name] = c
        if any(self._read(n) != c.after for n, c in last.items()):
            return False
        for name, c in first.items():
            if c.before is None:
                self.path(name).unlink(missing_ok=True)
            else:
                self.path(name).parent.mkdir(parents=True, exist_ok=True)
                self.path(name).write_bytes(c.before.encode("utf-8"))
        return True

    def run(self, tool: str, args: dict[str, Any]) -> tuple[CommandChange, dict[str, Any]]:
        """One command tool call: the change and the result the agent reads."""
        if tool == "save_command":
            change = self.save(
                args.get("name", ""),
                args.get("description", ""),
                args.get("body", ""),
                args.get("argument_hint", ""),
            )
            data = {
                "saved": change.qualified,
                "path": change.path.relative_to(self.root).as_posix(),
                "replaced": change.before is not None,
                "note": f"the user runs it from their next message as {change.qualified} "
                "[arguments]; its body then reaches you as their request",
            }
        elif tool == "delete_command":
            change = self.delete(args.get("name", ""))
            data = {"deleted": change.qualified}
        else:
            raise KeyError(tool)
        return change, data


__all__ = [
    "COMMANDS_DIR",
    "COMMAND_TOOLS",
    "CommandChange",
    "CommandStore",
    "command_text",
    "list_commands",
]
