"""Screener: ordered include/exclude rules compiled to regex over the POSIX relative path.

Rule bodies:

- ``@images`` -- a type alias from the locator manifests (extension set)
- ``.jpg,.json`` -- an explicit extension set
- ``*.jpg``, ``front/**/*.png`` -- a glob; without a ``/`` it matches the basename at any
  depth (gitignore style), with one it is anchored at the source root
- ``re:^front/\\d+_`` -- a raw regex, ``search``ed on the relative path (case-sensitive)

Aliases, extension sets and globs are case-insensitive (Windows and macOS file systems
are). Rules are evaluated in order and the **last** matching rule decides. A path no rule
matches is included when the screener has no include rules (exclude-only screener), and
excluded otherwise. An empty screener includes everything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from specto_core.errors import InvalidArgs

from .spec import Rule

RuleKind = Literal["alias", "ext", "glob", "regex"]


def glob_to_regex(pattern: str) -> str:
    """Translate a POSIX glob to an anchored regex. ``**`` spans directories, ``*`` and
    ``?`` stay inside one path segment, ``[...]`` classes pass through."""
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                i += 2
                if i < n and pattern[i] == "/":
                    out.append("(?:.*/)?")
                    i += 1
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            j = pattern.find("]", j)
            if j < 0:
                out.append("\\[")
            else:
                body = pattern[i + 1 : j].replace("\\", "\\\\")
                if body and body[0] == "!":
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def _ext_regex(exts: list[str]) -> str:
    alts = "|".join(re.escape(e.lstrip(".")) for e in sorted(set(exts)))
    return rf".*\.(?:{alts})$"


@dataclass(frozen=True)
class CompiledRule:
    index: int
    rule: Rule
    kind: RuleKind
    regex: re.Pattern[str]
    extensions: tuple[str, ...] = ()

    @property
    def action(self) -> str:
        return self.rule.action

    def matches(self, rel: str) -> bool:
        if self.kind == "regex":
            return self.regex.search(rel) is not None
        return self.regex.fullmatch(rel) is not None

    def describe(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "rule": self.rule.text(),
            "action": self.rule.action,
            "kind": self.kind,
            "regex": self.regex.pattern,
            "extensions": list(self.extensions),
        }


def compile_rule(index: int, rule: Rule, aliases: dict[str, list[str]]) -> CompiledRule:
    body = rule.pattern.strip()
    if not body:
        raise InvalidArgs(f"screener rule {index} is empty")
    if body.startswith("@"):
        exts = aliases.get(body)
        if exts is None:
            raise InvalidArgs(
                f"unknown type alias {body!r} in screener rule {index}",
                fix=f"known aliases: {', '.join(sorted(aliases)) or '(none registered)'}",
            )
        if not exts:
            raise InvalidArgs(f"type alias {body!r} has no extensions")
        return CompiledRule(
            index, rule, "alias", re.compile(_ext_regex(exts), re.IGNORECASE), tuple(exts)
        )
    if body.startswith("re:"):
        try:
            return CompiledRule(index, rule, "regex", re.compile(body[3:]))
        except re.error as e:
            raise InvalidArgs(
                f"screener rule {index} regex does not compile: {e}", fix="check the regex"
            ) from e
    parts = [p.strip() for p in body.split(",")]
    if all(p.startswith(".") and len(p) > 1 and "/" not in p and "*" not in p for p in parts):
        exts = [p.lower() for p in parts]
        return CompiledRule(
            index, rule, "ext", re.compile(_ext_regex(exts), re.IGNORECASE), tuple(exts)
        )
    glob = body.replace("\\", "/")
    if glob.startswith("./"):
        glob = glob[2:]
    anchored = "/" in glob
    if glob.startswith("/"):
        glob = glob.lstrip("/")
    regex = glob_to_regex(glob)
    if not anchored:
        regex = "(?:.*/)?" + regex
    return CompiledRule(index, rule, "glob", re.compile(regex, re.IGNORECASE))


@dataclass
class ScreenExplain:
    """Why a path was included or excluded."""

    path: str
    included: bool
    reason: str
    deciding: CompiledRule | None = None
    hits: list[CompiledRule] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "included": self.included,
            "reason": self.reason,
            "deciding_rule": self.deciding.describe() if self.deciding else None,
            "matched_rules": [r.describe() for r in self.hits],
        }


class Screener:
    def __init__(self, rules: list[Rule], aliases: dict[str, list[str]] | None = None) -> None:
        aliases = aliases or {}
        self.rules: list[CompiledRule] = [
            compile_rule(i, Rule.coerce(r), aliases) for i, r in enumerate(rules)
        ]
        self.has_includes = any(r.action == "include" for r in self.rules)

    def accepts(self, rel: str) -> bool:
        decision = not self.has_includes
        for r in self.rules:
            if r.matches(rel):
                decision = r.action == "include"
        return decision

    def explain(self, rel: str) -> ScreenExplain:
        hits = [r for r in self.rules if r.matches(rel)]
        if not hits:
            if not self.rules:
                return ScreenExplain(rel, True, "no screener rules: everything is included")
            if self.has_includes:
                return ScreenExplain(
                    rel, False, "no rule matched and the screener has include rules"
                )
            return ScreenExplain(rel, True, "no exclude rule matched (exclude-only screener)")
        last = hits[-1]
        verb = "included" if last.action == "include" else "excluded"
        return ScreenExplain(
            rel,
            last.action == "include",
            f"{verb} by rule {last.index}: {last.rule.text()}",
            last,
            hits,
        )

    def describe(self) -> list[dict[str, Any]]:
        return [r.describe() for r in self.rules]
