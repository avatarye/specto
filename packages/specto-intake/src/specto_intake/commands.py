"""Intake commands registered in the core registry (HLD 3.6, contracts section 10).

``intake.aliases`` the ``@alias`` table from the locator manifests
``intake.preview`` what a spec would intake, without creating anything (pure; job optional)
``intake.keys_preview`` named-group captures of a keys regex over sample names (pure)

All three are pure helpers for authoring an intaker's params; the intaker itself is a
link in the canvas document, resolved by ``specto_intake.resolve``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from specto_core import CommandContext, Engine, Result, command
from specto_core.registry import Args

from .preview import DEFAULT_LIMIT, DEFAULT_MAX_FILES, keys_preview, preview_job
from .preview import preview as preview_spec
from .scan import scan_env


def project_base(engine: Engine) -> Path:
    return engine.project.root if engine.project is not None else Path.cwd()


# -- intake.aliases ----------------------------------------------------------------------


class AliasesArgs(Args):
    pass


class AliasesResult(Result):
    aliases: dict[str, list[str]]


@command(
    "intake.aliases",
    doc="Type aliases usable in screener rules (@images, @json, ...) and their extensions.",
    read_only=True,
    scope="global",
)
def intake_aliases(ctx: CommandContext, args: AliasesArgs) -> AliasesResult:
    return AliasesResult(aliases=ctx.engine.plugins.aliases())


# -- intake.preview ----------------------------------------------------------------------


class IntakePreviewArgs(Args):
    spec: dict[str, Any] = Field(description="Intake spec to preview (nothing is created).")
    limit: int = Field(
        default=DEFAULT_LIMIT,
        ge=1,
        le=1000,
        description="How many matched, excluded, skipped paths and example items to return.",
    )
    max_files: int = Field(
        default=DEFAULT_MAX_FILES,
        ge=1,
        description="Stop walking after this many files (``truncated`` is set).",
    )
    background: bool = Field(
        default=False,
        description="Run as a job and return only {job}; job.status carries the result.",
    )


class IntakePreviewResult(Result):
    ok: bool = False
    job: str | None = None
    errors: list[str] = Field(default_factory=list)
    files: int = 0
    truncated: bool = False
    matched_count: int = 0
    excluded_count: int = 0
    matched: list[str] = Field(default_factory=list)
    excluded: list[dict[str, Any]] = Field(default_factory=list)
    skipped_count: int = 0
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    item_count: int = 0
    items: list[dict[str, Any]] = Field(default_factory=list)
    key_fields: list[str] = Field(default_factory=list)
    kinds: dict[str, int] = Field(default_factory=dict)
    parts: dict[str, int] = Field(default_factory=dict)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    elapsed_s: float = 0.0


@command(
    "intake.preview",
    doc="What a spec would intake: counts, first matched/excluded paths with the deciding "
    "rule, example items, kinds. Read-only; creates nothing.",
    read_only=True,
    scope="global",
)
def intake_preview(ctx: CommandContext, args: IntakePreviewArgs) -> IntakePreviewResult:
    engine = ctx.engine
    aliases, ext_kinds = scan_env(engine.plugins)
    if args.background:
        job = engine.jobs.submit(
            "intake.preview",
            preview_job,
            args.spec,
            str(project_base(engine)),
            aliases,
            ext_kinds,
            args.limit,
            args.max_files,
        )
        if job.state == "done" and isinstance(job.result, dict):  # inline manager
            return IntakePreviewResult(job=job.id, **job.result)
        return IntakePreviewResult(job=job.id)
    out = preview_spec(
        args.spec,
        base=project_base(engine),
        aliases=aliases,
        ext_kinds=ext_kinds,
        limit=args.limit,
        max_files=args.max_files,
    )
    return IntakePreviewResult(**out)


# -- intake.keys_preview -----------------------------------------------------------------


class KeysPreviewArgs(Args):
    regex: str | None = Field(
        default=None, description="Keys regex with named groups; empty = default stem key."
    )
    names: list[str] = Field(
        default_factory=list, description="Sample relative paths or file names to match."
    )


class KeysPreviewResult(Result):
    regex: str
    fields: list[str]
    rows: list[dict[str, Any]]
    matched: int
    unmatched: int
    error: str | None = None
    presets: list[dict[str, str]]


@command(
    "intake.keys_preview",
    doc="Named-group captures of a keys regex over sample names (a table), the preset "
    "regexes, and an error when the regex does not compile or names no group.",
    read_only=True,
    scope="global",
)
def intake_keys_preview(ctx: CommandContext, args: KeysPreviewArgs) -> KeysPreviewResult:
    return KeysPreviewResult(**keys_preview(args.regex, args.names))
