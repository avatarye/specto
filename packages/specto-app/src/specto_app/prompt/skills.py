"""Skills the prompt-bar agent writes for its own project (#161): ``save_skill`` and
``delete_skill`` over ``<project>/.specto/skills/<name>/SKILL.md``, which the session
loads as ``specto:<name>`` (the ``.specto`` plugin, ``session.ensure_plugin``).

The frontmatter is Specto's, not the agent's: ``name`` and ``description`` only, so a
skill can change the agent's instructions but never its permissions (no
``allowed-tools``; the agent has no shell or file tools and keeps it that way). Each
call returns a ``SkillChange`` carrying the file before and after, so the reply strip's
Undo restores a turn's skill changes in one step. No Qt here.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from specto_core.agent_tools import ToolSpec
from specto_core.errors import InvalidArgs, NotFound

PLUGIN = "specto"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_BODY = 20_000

SKILL_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "save_skill",
            "Save a skill for this project: a repeatable way of composing the user taught "
            "you (naming, which views suit which data, link recipes), as "
            ".specto/skills/<name>/SKILL.md. It loads as specto:<name> from the user's "
            "next message (invoke it with the Skill tool). Saving an existing name "
            "replaces that skill. Not for one-off requests.",
            params={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "kebab-case, at most 64 characters: front-run-layout",
                    },
                    "description": {
                        "type": "string",
                        "description": "One line: when to use the skill (the agent reads it "
                        "to decide).",
                    },
                    "body": {
                        "type": "string",
                        "description": "The instructions, in markdown (no frontmatter).",
                    },
                },
                "required": ["name", "description", "body"],
            },
        ),
        ToolSpec(
            "delete_skill",
            "Delete one of this project's skills (specto:<name>) by its name.",
            destructive=True,
            params={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "the skill's name"}},
                "required": ["name"],
            },
        ),
    )
}


@dataclass(frozen=True)
class SkillChange:
    """One skill file written or removed: its text before (``None``: it was new) and
    after (``None``: deleted)."""

    name: str
    path: Path
    before: str | None
    after: str | None

    @property
    def qualified(self) -> str:
        return f"{PLUGIN}:{self.name}"

    @property
    def action(self) -> str:
        return "deleted" if self.after is None else "saved"


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """``(meta, body)`` of a SKILL.md: ``key: value`` lines between the ``---`` fences,
    a value double-quoted (JSON, which YAML reads the same) or bare. ``InvalidArgs``
    when the fences or a line do not parse."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise InvalidArgs("the skill file has no frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise InvalidArgs("the skill file's frontmatter is not closed") from None
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            raise InvalidArgs(f"frontmatter line does not parse: {line!r}")
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                raise InvalidArgs(f"frontmatter value does not parse: {line!r}") from None
        meta[key.strip()] = value
    return meta, "\n".join(lines[end + 1 :]).lstrip("\n")


def skill_text(name: str, description: str, body: str) -> str:
    desc = json.dumps(description, ensure_ascii=False)
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}"


def list_skills(root: Path) -> list[dict[str, str]]:
    """``[{name, description}]`` of every project skill (``.claude/skills`` as
    ``<name>``, ``.specto/skills`` as ``specto:<name>``), sorted by name."""
    out: list[dict[str, str]] = []
    for where, prefix in ((".claude", ""), (".specto", f"{PLUGIN}:")):
        for path in sorted((Path(root) / where / "skills").glob("*/SKILL.md")):
            try:
                meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, InvalidArgs):
                meta = {}
            out.append(
                {"name": prefix + path.parent.name, "description": meta.get("description", "")}
            )
    return sorted(out, key=lambda s: s["name"])


def skills_key(root: Path) -> tuple[tuple[str, int, int], ...]:
    """What the running session loaded: every project SKILL.md and every project
    command file (``.claude/commands/*.md``, ``.specto/commands/*.md``; #219) with its
    mtime and size. A different key after a turn means a skill or command was added,
    changed or removed: the CLI fixes both at connect, so the session reconnects."""
    out: list[tuple[str, int, int]] = []
    for where in (".claude", ".specto"):
        base = Path(root) / where
        paths = [*base.glob("skills/*/SKILL.md"), *base.glob("commands/*.md")]
        for path in sorted(paths):
            try:
                st = path.stat()
            except OSError:
                continue
            out.append((path.relative_to(root).as_posix(), st.st_mtime_ns, st.st_size))
    return tuple(out)


class SkillStore:
    """The project's ``.specto/skills``: validated writes, deletes and their undo."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dir = self.root / ".specto" / "skills"

    def path(self, name: str) -> Path:
        return self.dir / name / "SKILL.md"

    def _read(self, name: str) -> str | None:
        try:
            return self.path(name).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def _check_name(self, name: str) -> None:
        if len(name) > MAX_NAME:
            raise InvalidArgs(f"skill name {name[:20]!r}... is longer than {MAX_NAME} characters")
        if not NAME_RE.match(name):
            raise InvalidArgs(
                f"skill name {name!r} is not kebab-case",
                fix="lowercase letters and digits joined by single hyphens: front-run-layout",
            )

    def save(self, name: str, description: str, body: str) -> SkillChange:
        name, description = str(name).strip(), str(description).strip()
        self._check_name(name)
        if not description:
            raise InvalidArgs("the skill needs a description", fix="one line: when to use it")
        if "\n" in description or "\r" in description:
            raise InvalidArgs("the description must be one line")
        if len(description) > MAX_DESCRIPTION:
            raise InvalidArgs(f"the description is longer than {MAX_DESCRIPTION} characters")
        body = str(body).replace("\r\n", "\n")
        if not body.strip():
            raise InvalidArgs("the skill's body is empty", fix="write the instructions")
        if len(body) > MAX_BODY:
            raise InvalidArgs(f"the skill's body is longer than {MAX_BODY} characters")
        if body.lstrip().startswith("---"):
            raise InvalidArgs(
                "the body starts with its own frontmatter",
                fix="pass the instructions alone; Specto writes the frontmatter (name and "
                "description only: a skill cannot declare allowed-tools)",
            )
        if not body.endswith("\n"):
            body += "\n"
        text = skill_text(name, description, body)
        meta, _ = parse_frontmatter(text)
        if meta != {"name": name, "description": description}:
            raise InvalidArgs("the skill's frontmatter does not parse back")
        before = self._read(name)
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return SkillChange(name, path, before, text)

    def delete(self, name: str) -> SkillChange:
        name = str(name).strip()
        self._check_name(name)
        before = self._read(name)
        if before is None:
            have = ", ".join(p.parent.name for p in sorted(self.dir.glob("*/SKILL.md")))
            raise NotFound(f"no skill {name!r} in this project", fix=f"skills: {have or 'none'}")
        shutil.rmtree(self.path(name).parent)
        return SkillChange(name, self.path(name), before, None)

    def undo(self, changes: list[SkillChange]) -> bool:
        """Put every changed skill back as it was before ``changes`` (one turn's), newest
        first. Refused, changing nothing, when a file no longer holds what the turn
        left there (edited since)."""
        last: dict[str, SkillChange] = {}
        first: dict[str, SkillChange] = {}
        for c in changes:
            first.setdefault(c.name, c)
            last[c.name] = c
        if any(self._read(n) != c.after for n, c in last.items()):
            return False
        for name, c in first.items():
            if c.before is None:
                shutil.rmtree(self.path(name).parent, ignore_errors=True)
            else:
                self.path(name).parent.mkdir(parents=True, exist_ok=True)
                self.path(name).write_bytes(c.before.encode("utf-8"))
        return True

    def run(self, tool: str, args: dict[str, Any]) -> tuple[SkillChange, dict[str, Any]]:
        """One skill tool call: the change and the result the agent reads."""
        if tool == "save_skill":
            change = self.save(
                args.get("name", ""), args.get("description", ""), args.get("body", "")
            )
            data = {
                "saved": change.qualified,
                "path": change.path.relative_to(self.root).as_posix(),
                "replaced": change.before is not None,
                "note": f"loads from the user's next message: invoke it with the Skill tool as "
                f"{change.qualified}",
            }
        elif tool == "delete_skill":
            change = self.delete(args.get("name", ""))
            data = {"deleted": change.qualified}
        else:
            raise KeyError(tool)
        return change, data


__all__ = [
    "SKILL_TOOLS",
    "SkillChange",
    "SkillStore",
    "list_skills",
    "parse_frontmatter",
    "skill_text",
    "skills_key",
]
