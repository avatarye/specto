"""The canvas context every prompt carries (spec #35 story 10): the canvas document,
the user's viewport, the mouse and the selections, so "show these" and "next to this"
resolve. Rendered as one fenced JSON block ahead of the user's own words."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CanvasContext:
    #: the whole canvas document (``None``: unchanged since the agent last saw it)
    document: dict[str, Any] | None
    #: the canvas area as a canvas rect ``{x, y, w, h}`` in canvas px
    viewport: dict[str, float]
    #: ``{x, y, over}`` in canvas px with the view under the pointer, or ``None`` when the
    #: pointer is outside the canvas
    mouse: dict[str, Any] | None = None
    #: per view, its selection-typed output ports (``{keys, last}``) and the files
    #: picked in a file tree (``selected_files: {count, paths}``)
    selection: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: the views the user has selected on the canvas (header clicks, the marquee), in
    #: selection order, so "make these bigger" names them
    selected_views: list[str] = field(default_factory=list)
    #: the project folder's files folder by folder: ``{project: {root, count,
    #: folders: {folder: {ext: n}}}}``, so a request can name real paths; with many
    #: folders also ``depth``, ``deeper`` and ``note`` (``project_listing.folder_summary``)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: the hash of the live document, so an omitted document is still identifiable
    document_hash: str = ""
    #: the project's skills (``[{name, description}]``) when they changed since the
    #: agent's last turn other than by its own save_skill / delete_skill (#161): a
    #: resumed session does not list them again; ``None`` when unchanged
    skills: list[dict[str, str]] | None = None
    #: the project's slash commands (``[{name, description}]``, ``/<name>``) when they or
    #: the skills changed since the agent's last turn (#219); ``None`` when unchanged
    commands: list[dict[str, str]] | None = None
    #: the project's scratch pad (#184): its pages in order, ``[{page, name, chars,
    #: active?, headings?}]`` (scratch_read gives a page's markdown); empty when the pad
    #: is one empty page
    scratch: list[dict[str, Any]] = field(default_factory=list)


def document_hash(document: dict[str, Any]) -> str:
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha1(raw).hexdigest()[:12]


def render_context(ctx: CanvasContext) -> str:
    """The context block the agent reads before the prompt: a short heading, then the
    JSON. The document travels in full when it changed since the agent's last turn,
    otherwise its hash (the agent's own copy is current: no ``state`` call needed)."""
    body: dict[str, Any] = {
        "viewport": ctx.viewport,
        "mouse": ctx.mouse,
        "selection": ctx.selection,
        "selected_views": ctx.selected_views,
        "files": ctx.files,
    }
    if ctx.document is not None:
        body["document"] = ctx.document
        body["document_hash"] = ctx.document_hash or document_hash(ctx.document)
        note = "the canvas document is included (it changed since your last turn): it is the state"
    else:
        body["document_hash"] = ctx.document_hash
        note = (
            "the canvas document is unchanged since your last turn: the one you last saw or "
            "submitted is still current"
        )
    if ctx.scratch:
        body["scratch"] = ctx.scratch
        note += (
            "; scratch lists the scratch pad's pages with their size and headings "
            "(scratch_read for a page's markdown)"
        )
    if ctx.skills is not None:
        body["skills"] = ctx.skills
        note += (
            "; the project's skills changed since your last turn: skills lists them all now "
            "(invoke one with the Skill tool by its name)"
        )
    if ctx.commands is not None:
        body["commands"] = ctx.commands
        note += (
            "; commands lists the project's slash commands, which the user runs from the "
            "prompt bar as /<name> [arguments]"
        )
    return (
        "Canvas context (canvas px; the viewport is what the user sees, mouse.over the "
        "view under the pointer; selection.<file tree>.selected_files is what the user has "
        "picked, which selection-scoped intakers follow as it changes; selected_views are "
        'the views the user has selected on the canvas, "these" in a request): '
        f"{note}.\n```json\n"
        + json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        + "\n```"
    )


__all__ = ["CanvasContext", "document_hash", "render_context"]
