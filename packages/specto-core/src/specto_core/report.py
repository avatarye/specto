"""Conversion report (HLD 3.4, 3.7; R8a).

Handlers append entries while they run; a transaction aggregates them. ``txn.commit
--dry-run`` returns the report with the forward patch. Entries say what bound, what was
skipped and why, which quota would be hit and which effects would happen.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EntryKind = Literal["bound", "skipped", "quota", "effect", "warning", "error", "info"]


class ReportEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: EntryKind
    object: str | None = None  # object id the entry is about
    command: str | None = None
    message: str
    fix: str | None = None
    count: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ConversionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[ReportEntry] = Field(default_factory=list)

    def add(
        self,
        entry_kind: EntryKind,
        message: str,
        *,
        object: str | None = None,
        command: str | None = None,
        fix: str | None = None,
        count: int | None = None,
        **detail: Any,
    ) -> ReportEntry:
        e = ReportEntry(
            kind=entry_kind,
            object=object,
            command=command,
            message=message,
            fix=fix,
            count=count,
            detail=detail,
        )
        self.entries.append(e)
        return e

    def bound(self, message: str, **kw: Any) -> ReportEntry:
        return self.add("bound", message, **kw)

    def skipped(self, message: str, **kw: Any) -> ReportEntry:
        return self.add("skipped", message, **kw)

    def warning(self, message: str, **kw: Any) -> ReportEntry:
        return self.add("warning", message, **kw)

    def error(self, message: str, **kw: Any) -> ReportEntry:
        return self.add("error", message, **kw)

    def extend(self, other: ConversionReport) -> None:
        self.entries.extend(other.entries)

    @property
    def ok(self) -> bool:
        return not any(e.kind == "error" for e in self.entries)

    def by_kind(self, kind: EntryKind) -> list[ReportEntry]:
        return [e for e in self.entries if e.kind == kind]

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out

    def text(self) -> str:
        """Plain-text rendering for CLI output. ASCII indicators only."""
        tags = {
            "bound": "[OK]",
            "skipped": "[SKIP]",
            "quota": "[QUOTA]",
            "effect": "[EFFECT]",
            "warning": "[WARN]",
            "error": "[ERROR]",
            "info": "[INFO]",
        }
        lines = []
        for e in self.entries:
            who = f" {e.object}" if e.object else ""
            cnt = f" x{e.count}" if e.count is not None else ""
            fix = f"  -> {e.fix}" if e.fix else ""
            lines.append(f"{tags[e.kind]}{who}{cnt}: {e.message}{fix}")
        return "\n".join(lines)
