"""Intake spec (HLD 3.6): where the data is and how it becomes items.

``{sources: [{root, pattern?, depth?, follow_links?}], screener: [rules], keys: regex,
group_by: [field], sort, watch, merge_roots}``. The spec is plain data; the document
stores ``IntakeSpec.model_dump()`` under ``col.spec``.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from specto_core.errors import InvalidArgs
from specto_core.pathguard import Refusal, path_refusal

SortMode = Literal["natural", "key", "mtime"]
RuleAction = Literal["include", "exclude"]

_GLOB_MAGIC = re.compile(r"[*?\[]")


def root_refused(root: str, why: Refusal) -> InvalidArgs:
    """The error for a source root the path guard refuses (``specto_core.pathguard``,
    SPEC-72): a network path (on Windows a stat of it connects out over SMB), a device
    path or a rooted path that is no drive path. Raised by the spec's validation and,
    as the backstop, by ``sources.resolve_root`` before any filesystem call."""
    return InvalidArgs(
        f"source root {root!r} is {why}",
        fix="name a path or glob under the project folder ('.' is the whole folder) or an "
        "absolute local path; a network, device or rooted path is never looked up",
    )


class SourceSpec(BaseModel):
    """One place to look: a file, a directory, or a glob below a directory."""

    model_config = ConfigDict(extra="forbid")
    root: str = Field(description="File or directory; relative paths resolve against the project.")
    pattern: str | None = Field(
        default=None, description="Glob relative to root (``**/*.jpg``). None = everything."
    )
    depth: int | None = Field(
        default=None,
        ge=0,
        description="Recursion depth below root; None = unlimited, 0 = root only.",
    )
    follow_links: bool = False

    @field_validator("root")
    @classmethod
    def _local_root(cls, v: str) -> str:
        """A root is judged by its text before anything resolves or stats it (SPEC-72)."""
        why = path_refusal(v)
        if why is not None:
            raise root_refused(v, why)
        return v

    @classmethod
    def coerce(cls, value: Any) -> SourceSpec:
        """``"runs/"`` -> dir; ``"runs/**/*.jpg"`` -> root ``runs`` + pattern ``**/*.jpg``."""
        if isinstance(value, SourceSpec):
            return value
        if isinstance(value, str):
            # the whole text first, as the canvas validator judges it (SPEC-79): an NT
            # object spelling (``\??\UNC\host``) has glob magic at its second
            # character and would otherwise split into a pattern under the project folder
            why = path_refusal(value)
            if why is not None:
                raise root_refused(value, why)
            text = value.replace("\\", "/")
            m = _GLOB_MAGIC.search(text)
            if m is None:
                return cls(root=text)
            cut = text.rfind("/", 0, m.start())
            root = text[:cut] if cut > 0 else "."
            return cls(root=root, pattern=text[cut + 1 :])
        if isinstance(value, dict):
            return cls.model_validate(value)
        raise InvalidArgs(f"source must be a path string or an object, got {type(value).__name__}")


class Rule(BaseModel):
    """One screener rule. String forms: ``@images``, ``*.jpg``, ``.jpg,.json``,
    ``re:^front/``; prefix ``!`` (or ``-``) excludes, ``+`` includes (the default)."""

    model_config = ConfigDict(extra="forbid")
    pattern: str
    action: RuleAction = "include"

    @classmethod
    def coerce(cls, value: Any) -> Rule:
        if isinstance(value, Rule):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                raise InvalidArgs("empty screener rule")
            if text[0] in "!-":
                return cls(pattern=text[1:].strip(), action="exclude")
            if text[0] == "+":
                return cls(pattern=text[1:].strip(), action="include")
            return cls(pattern=text, action="include")
        if isinstance(value, dict):
            if "include" in value and len(value) == 1:
                return cls(pattern=str(value["include"]), action="include")
            if "exclude" in value and len(value) == 1:
                return cls(pattern=str(value["exclude"]), action="exclude")
            return cls.model_validate(value)
        raise InvalidArgs(
            f"screener rule must be a string or an object, got {type(value).__name__}"
        )

    def text(self) -> str:
        return ("!" if self.action == "exclude" else "") + self.pattern


class IntakeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[SourceSpec] = Field(min_length=1)
    screener: list[Rule] = Field(default_factory=list)
    keys: str | None = Field(
        default=None,
        description="Regex with named groups matched against the POSIX path relative to the "
        "source root; None keys by path without extension (sidecars group).",
    )
    group_by: list[str] = Field(
        default_factory=list, description="Key fields that define an item; default: all groups."
    )
    sort: SortMode = "natural"
    watch: bool = False
    merge_roots: bool = Field(
        default=False, description="Let keys from different sources collide into one item."
    )

    @field_validator("sources", mode="before")
    @classmethod
    def _sources(cls, v: Any) -> list[SourceSpec]:
        if isinstance(v, str | dict | SourceSpec):
            v = [v]
        if not isinstance(v, list):
            raise InvalidArgs("sources must be a list")
        return [SourceSpec.coerce(s) for s in v]

    @field_validator("screener", mode="before")
    @classmethod
    def _screener(cls, v: Any) -> list[Rule]:
        if v is None:
            return []
        if isinstance(v, str | dict | Rule):
            v = [v]
        if not isinstance(v, list):
            raise InvalidArgs("screener must be a list of rules")
        return [Rule.coerce(r) for r in v]

    @field_validator("keys")
    @classmethod
    def _keys(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        try:
            re.compile(v)
        except re.error as e:
            raise InvalidArgs(f"keys regex does not compile: {e}", fix="check the regex") from e
        return v

    @model_validator(mode="after")
    def _group_fields(self) -> IntakeSpec:
        if self.keys is None:
            if self.group_by and self.group_by != ["stem"]:
                raise InvalidArgs(
                    f"group_by {self.group_by} needs a keys regex with those named groups",
                    fix="set keys to a regex with (?P<name>...) groups, or drop group_by",
                )
            return self
        names = list(re.compile(self.keys).groupindex)
        if not names:
            raise InvalidArgs(
                "keys regex has no named groups",
                fix="name the groups: (?P<frame>\\d+)",
            )
        missing = [g for g in self.group_by if g not in names]
        if missing:
            raise InvalidArgs(
                f"group_by fields {missing} are not groups of the keys regex",
                fix=f"available: {', '.join(names)}",
            )
        return self

    def key_fields(self) -> list[str]:
        """The fields that make the item key, in order."""
        if self.keys is None:
            return ["stem"]
        return list(self.group_by) or list(re.compile(self.keys).groupindex)

    @classmethod
    def parse(cls, raw: Any) -> IntakeSpec:
        if isinstance(raw, IntakeSpec):
            return raw
        try:
            return cls.model_validate(raw)
        except InvalidArgs:
            raise
        except Exception as e:  # pydantic ValidationError
            raise InvalidArgs(f"invalid intake spec: {e}", fix="describe link:intaker") from e


def intake_spec(params: dict[str, Any]) -> IntakeSpec:
    """The canvas document's ``IntakerParams`` as an ``IntakeSpec``."""
    return IntakeSpec(
        sources=list(params.get("sources") or ["."]),
        screener=list(params.get("screener") or []),
        keys=params.get("keys"),
        group_by=list(params.get("group_by") or []),
        sort=params.get("sort", "natural"),
        watch=bool(params.get("watch", False)),
    )
