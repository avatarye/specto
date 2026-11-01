"""Wall-clock stamps for journal entries, events, jobs and trust records."""

from __future__ import annotations

from datetime import UTC, datetime


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
