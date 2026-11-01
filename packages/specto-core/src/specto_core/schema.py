"""Canvas document schema version and forward-only migrations (HLD 3.2).

``SCHEMA_VERSION`` is the version this core writes. ``migrate(data)`` brings an older
document forward in memory, one step at a time, through functions registered with
``@migration(n)`` (meaning ``n -> n+1``). A newer document is refused with both
version numbers in the message.

Version 2 is the canvas document v2 (``canvas_document.py``). There is no migration from
version 1 (ADR 0017): v1 projects are refused on open; the machinery stays for later
versions.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .errors import SchemaError

SCHEMA_VERSION = 2
V1_FIX = (
    "v2 does not convert v1 (M2) projects: open a different folder, "
    "or remove .specto/ to start fresh"
)

Migration = Callable[[dict[str, Any]], dict[str, Any]]
_MIGRATIONS: dict[int, Migration] = {}


def migration(from_version: int) -> Callable[[Migration], Migration]:
    """Register a migration ``from_version -> from_version + 1``."""

    def deco(fn: Migration) -> Migration:
        if from_version in _MIGRATIONS:
            raise ValueError(f"migration from {from_version} already registered")
        _MIGRATIONS[from_version] = fn
        return fn

    return deco


def registered_migrations() -> dict[int, Migration]:
    return dict(_MIGRATIONS)


def migrate(data: dict[str, Any], *, target: int = SCHEMA_VERSION) -> tuple[dict[str, Any], bool]:
    """Return ``(data, migrated)``. ``data`` is mutated in place by each step."""
    v = data.get("schema_version")
    if not isinstance(v, int):
        raise SchemaError(
            "document has no integer schema_version",
            fix='add "schema_version": <int> at the document root',
        )
    if v > target:
        raise SchemaError(
            f"document schema_version {v} is newer than this core understands ({target})",
            fix="upgrade specto-core, or open the file with the version that wrote it",
            file_version=v,
            core_version=target,
        )
    migrated = False
    while v < target:
        step = _MIGRATIONS.get(v)
        if step is None:
            if v <= 1 and target >= 2:
                raise SchemaError(
                    f"this canvas document is schema_version {v}, a Specto v1 (M2) canvas",
                    fix=V1_FIX,
                    file_version=v,
                    core_version=target,
                )
            raise SchemaError(
                f"no migration from schema_version {v} to {v + 1}",
                file_version=v,
                core_version=target,
            )
        data = step(data)
        v += 1
        data["schema_version"] = v
        migrated = True
    return data, migrated
