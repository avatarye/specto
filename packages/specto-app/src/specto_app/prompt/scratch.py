"""The scratch pad's agent tools (#184) over the engine's ``ScratchStore``
(``.specto/scratch``), the same pages the user drops into: the page markdown
(``scratch_read``, ``scratch_append``, ``scratch_write``) and the named pages
(``scratch_pages``, ``scratch_page_add``, ``scratch_page_rename``,
``scratch_page_remove``). A page is named by its id (p2) or its name; tools that take
one default to the active page (the one the view shows).

Each change is a step on the store's undo stack with the agent's origin; the prompt
turn folds its steps into one (``TurnTools.end``), which the thread's scratch row undoes
(and Ctrl+Z, in time order with the canvas). The context of every prompt names the
pages, with their size and headings (``context_items``); ``scratch_read`` gives a page's
markdown. A picture the agent adds (``scratch_append`` with ``image``) is stored as png
in the page's assets and embedded; a file (``file``) is linked, never copied. Which page
the view shows is its ``params.page`` (a submit_canvas edit), not a tool.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from specto_core.agent_tools import ToolSpec
from specto_core.errors import InvalidArgs, NotFound
from specto_core.pathguard import path_refusal
from specto_core.scratch import NewItem, ScratchStep, ScratchStore

#: the most of a page ``scratch_read`` returns
READ_CHARS = 40_000
#: headings per page the context names
CONTEXT_HEADINGS = 8
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

_PAGE = {
    "type": "string",
    "description": "A page by id (p2) or name; the active page (the one shown) when left out.",
}

SCRATCH_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "scratch_read",
            "Read a scratch pad page's markdown, with the files and pictures it links "
            "(paths relative to the page, in .specto/scratch).",
            read_only=True,
            params={"type": "object", "properties": {"page": _PAGE}},
        ),
        ToolSpec(
            "scratch_append",
            "Append to a scratch pad page as its own block: markdown, and/or image (a "
            "picture file's path: stored as png in the page's assets and embedded) and/or "
            "file (a path: linked, not copied); paths are relative to the project or "
            "absolute. One undo step.",
            params={
                "type": "object",
                "properties": {
                    "markdown": {"type": "string", "description": "Markdown to add."},
                    "image": {"type": "string", "description": "A picture file to embed."},
                    "file": {"type": "string", "description": "A file to link."},
                    "label": {
                        "type": "string",
                        "description": "The picture's or the link's text (default: the "
                        "file's name).",
                    },
                    "page": _PAGE,
                },
            },
        ),
        ToolSpec(
            "scratch_write",
            "Replace a scratch pad page's whole markdown (read it first; keep what the "
            "user wrote unless asked). One undo step.",
            params={
                "type": "object",
                "properties": {"markdown": {"type": "string"}, "page": _PAGE},
                "required": ["markdown"],
            },
        ),
        ToolSpec(
            "scratch_pages",
            "List the scratch pad's pages (tabs) in order: id, name, size, which is active "
            "(shown) and the path of its markdown file.",
            read_only=True,
        ),
        ToolSpec(
            "scratch_page_add",
            "Add a named page (tab) to the scratch pad; it becomes the active one. One undo step.",
            params={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "A few words, unique on the pad."},
                    "at": {"type": "integer", "description": "Its place (0: first); the end."},
                },
                "required": ["name"],
            },
        ),
        ToolSpec(
            "scratch_page_rename",
            "Rename a scratch pad page. One undo step.",
            params={
                "type": "object",
                "properties": {"page": _PAGE, "name": {"type": "string"}},
                "required": ["page", "name"],
            },
        ),
        ToolSpec(
            "scratch_page_remove",
            "Close a scratch pad page (the user can undo it); the pad keeps at least one page.",
            destructive=True,
            params={"type": "object", "properties": {"page": _PAGE}, "required": ["page"]},
        ),
    )
}


def headings_of(text: str, limit: int = CONTEXT_HEADINGS) -> list[str]:
    """``["# Title", "## Section"]`` of a page (fenced code skipped)."""
    out: list[str] = []
    fence = False
    for line in text.splitlines():
        if line.startswith(("```", "~~~")):
            fence = not fence
            continue
        m = None if fence else HEADING_RE.match(line)
        if m:
            out.append(f"{m.group(1)} {m.group(2)}")
            if len(out) >= limit:
                break
    return out


def context_items(store: ScratchStore) -> list[dict[str, Any]]:
    """What every prompt's context says of the scratch pad: its pages in order, each
    with its id, name, size in characters, its headings, and whether it is shown. Empty
    when the pad is one empty page."""
    if store.is_empty():
        return []
    out: list[dict[str, Any]] = []
    for p in store.pages:
        text = store.text(p["id"])
        entry: dict[str, Any] = {"page": p["id"], "name": p["name"], "chars": len(text)}
        if p["id"] == store.active:
            entry["active"] = True
        heads = headings_of(text)
        if heads:
            entry["headings"] = heads
        out.append(entry)
    return out


class ScratchTools:
    """The scratch tools over the store ``store()`` returns (the engine's, made on first
    use). ``to_png`` turns a picture file's bytes into png (None: not a picture)."""

    def __init__(
        self,
        store: Callable[[], ScratchStore],
        *,
        to_png: Callable[[bytes], bytes | None] | None = None,
        has_view: Callable[[], bool] | None = None,
    ) -> None:
        self._store = store
        self._to_png = to_png
        self._has_view = has_view

    @property
    def store(self) -> ScratchStore:
        return self._store()

    def _path(self, value: Any, what: str) -> Path:
        text = str(value or "").strip()
        if not text:
            raise InvalidArgs(f"the {what} needs a path")
        why = path_refusal(text)
        if why:  # judged by its text before is_file asks Windows (SPEC-73)
            raise InvalidArgs(f"{text} is {why}", fix="give a file on this machine")
        p = Path(text)
        p = p if p.is_absolute() else self.store.root / p
        if not p.is_file():
            raise NotFound(f"no file {text}", fix="list_files shows the project's files")
        return p

    def _png(self, path: Path) -> bytes:
        data = path.read_bytes()
        png = self._to_png(data) if self._to_png is not None else data
        if png is None or not png.startswith(b"\x89PNG"):
            raise InvalidArgs(f"{path.name} is not a picture", fix="link it as file instead")
        return png

    def run(
        self, tool: str, args: dict[str, Any], *, origin: str = "agent:prompt"
    ) -> tuple[ScratchStep | None, dict[str, Any]]:
        """One scratch tool call: the step it made (None: a read, or nothing changed)
        and the result the agent reads."""
        store = self.store
        page = args.get("page") or None
        if tool == "scratch_read":
            p = store.page(page)
            text = store.text(p["id"])
            out: dict[str, Any] = {**store.page_summary(p), "markdown": text[:READ_CHARS]}
            if len(text) > READ_CHARS:
                out["truncated"] = len(text)
            out["links"] = [
                {"target": ln["target"], "image": ln["image"], "label": ln["label"]}
                for ln in store.links(p["id"])
            ]
            return None, out
        if tool == "scratch_append":
            pid = store.page(page)["id"]
            entries: list[NewItem] = []
            label = args.get("label") or None
            if args.get("image"):
                path = self._path(args["image"], "image")
                entries.append(NewItem(kind="image", png=self._png(path), label=label or path.stem))
            if args.get("file"):
                path = self._path(args["file"], "file")
                entries.append(NewItem(kind="file", path=str(path), label=label))
            text = str(args.get("markdown") or "")
            extra, assets = store.markdown_of(entries, pid) if entries else ("", [])
            piece = "\n\n".join(b for b in (text.strip("\n"), extra) if b.strip())
            if not piece.strip():
                raise InvalidArgs(
                    "nothing to append", fix="give markdown, an image or a file to add"
                )
            step = store.append(piece, page=pid, assets=assets, origin=origin)
            data = {"page": store.page_summary(store.page(pid)), "added": piece}
            return step, self._noted(data)
        if tool == "scratch_write":
            if args.get("markdown") is None:
                raise InvalidArgs("give the page's whole markdown")
            pid = store.page(page)["id"]
            step = store.write(str(args["markdown"]), page=pid, origin=origin)
            return step, {"page": store.page_summary(store.page(pid)), "changed": step is not None}
        if tool == "scratch_pages":
            return None, {"pages": [store.page_summary(p) for p in store.pages]}
        if tool == "scratch_page_add":
            step = store.add_page(str(args.get("name") or ""), at=args.get("at"), origin=origin)
            return step, self._noted({"added": store.page_summary(store.page(store.active))})
        if tool == "scratch_page_rename":
            ref = store.page(page)["id"]
            step = store.rename_page(ref, str(args.get("name") or ""), origin=origin)
            return step, {"renamed": store.page_summary(store.page(ref))}
        if tool == "scratch_page_remove":
            if not page:
                raise InvalidArgs("name the page to close", fix="scratch_pages lists them")
            ref = store.page(page)
            step = store.remove_page(ref["id"], origin=origin)
            return step, {"removed": ref["id"], "pages": [p["id"] for p in store.pages]}
        raise KeyError(tool)

    def _noted(self, data: dict[str, Any]) -> dict[str, Any]:
        if self._has_view is not None and not self._has_view():
            data["note"] = (
                "the canvas has no scratch pad view: the change is kept; add a view of type "
                "scratch if the user wants to see the pad"
            )
        return data


def turn_label(labels: list[str]) -> str:
    """One line for a turn's scratch changes (the folded step's label)."""
    if not labels:
        return ""
    if len(labels) <= 2:
        return "; ".join(labels)
    return f"{labels[0]}; and {len(labels) - 1} more changes"


__all__ = ["SCRATCH_TOOLS", "ScratchTools", "context_items", "headings_of", "turn_label"]
