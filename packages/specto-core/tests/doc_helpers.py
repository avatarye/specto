"""Test documents: every view must hang off the project source by links (#83), so a
test that adds a view by hand pins it from ``project.all_files`` with ``pin``."""

from __future__ import annotations

from typing import Any

from specto_core.canvas_document import next_id

#: the input port and link kind a view type takes files on
_FILE_INPUT: dict[str, tuple[str, str]] = {"map": ("features", "read_contents")}


def pin(
    doc: dict[str, Any],
    vid: str,
    *,
    lid: str | None = None,
    sources: list[str] | None = None,
) -> str:
    """Add a pinned link from the project source into ``vid``'s file input (an intaker,
    or read_contents for a map). Returns the link id."""
    port, kind = _FILE_INPUT.get(doc["views"][vid]["type"], ("data", "intaker"))
    lid = lid or next_id(doc["links"], "l")
    link: dict[str, Any] = {
        "kind": kind,
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": vid, "port": port},
        "params": {"sources": sources or ["."]},
        "summary": {"sentence": f"The {vid} view always shows its sources."},
    }
    if kind == "intaker":
        link["scope"] = "pinned"
    doc["links"][lid] = link
    return lid
