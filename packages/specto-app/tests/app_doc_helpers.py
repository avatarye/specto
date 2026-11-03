"""Test documents: every view must hang off the project source by links (#83), so a
test that adds a view by hand pins it from ``project.all_files`` with ``pin``."""

from __future__ import annotations

from typing import Any

from specto_core.canvas_document import next_id
from specto_core.ports import file_input_of


def pin_link(vid: str, vtype: str = "gallery", sources: list[str] | None = None) -> dict[str, Any]:
    """A pinned link from the project source into a view's file input (an intaker, or
    read_contents for a map)."""
    port, kind = file_input_of(vtype) or ("data", "intaker")
    link: dict[str, Any] = {
        "kind": kind,
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": vid, "port": port},
        "params": {"sources": sources or ["."]},
        "summary": {"sentence": f"The {vid} view always shows its sources."},
    }
    if kind == "intaker":
        link["scope"] = "pinned"
    return link


def pin(
    doc: dict[str, Any], vid: str, *, lid: str | None = None, sources: list[str] | None = None
) -> str:
    """Add ``pin_link`` for ``doc``'s view ``vid`` under the next free link id (or ``lid``).
    Returns the link id."""
    lid = lid or next_id(doc["links"], "l")
    doc["links"][lid] = pin_link(vid, doc["views"][vid]["type"], sources)
    return lid
