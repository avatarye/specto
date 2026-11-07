"""The agent tool set (ADR 0017): state, describe, schema, submit_canvas, snap, undo.

One table (``AGENT_TOOLS``) and one dispatcher (``AgentTools``), so the prompt bar's
in-process agent and external MCP clients get the same six tools over the same
commands. ``AgentTools`` takes a ``call(name, args)`` function: ``engine.call`` bound to
a ``CallContext`` in-process, an SDK client's ``call`` over the socket.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import InvalidArgs, NotFound, SpectoError

CallFn = Callable[[str, dict[str, Any]], dict[str, Any]]

_NO_ARGS: dict[str, Any] = {"type": "object", "properties": {}}


@dataclass(frozen=True)
class ToolSpec:
    """One agent tool: its name, doc and JSON argument schema, the one source for the
    in-process SDK tools (``specto_app.prompt.session``) and the MCP server."""

    name: str
    doc: str
    read_only: bool = False
    destructive: bool = False
    params: dict[str, Any] = field(default_factory=lambda: dict(_NO_ARGS))

    def arg(self, name: str) -> dict[str, Any]:
        return dict(self.params.get("properties", {}).get(name, {}))

    def required(self) -> list[str]:
        return list(self.params.get("required", []))


AGENT_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "state",
            "The running canvas: its whole canvas document (views, links, layout, root "
            "view) and the undo state. Read it before composing when you do not have the "
            "document already. Views are keyed by their name (kobi, tamu; the file tree "
            "has one too, its type is file_tree): call them by name to the user and in "
            "links.",
            read_only=True,
        ),
        ToolSpec(
            "describe",
            "Describe a view type ('view:gallery': params and ports), a link kind "
            "('link:intaker': its params, scopes selection/pinned, one example each), a "
            "port type ('port:selection'), 'canvas' or a command, with its schema and an "
            "example.",
            read_only=True,
            params={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "'view:gallery', 'link:intaker', 'port:selection', "
                        "'canvas' or a command name",
                    }
                },
                "required": ["target"],
            },
        ),
        ToolSpec(
            "schema",
            "The canvas document's JSON Schema and a worked example document.",
            read_only=True,
        ),
        ToolSpec(
            "submit_canvas",
            "Change the canvas with edits (preferred) or a whole document, exactly one. "
            "edits names only what changes, in the document's own shape (a JSON Merge Patch "
            "over the running canvas): objects merge key by key, any other value (a list "
            "too) replaces, null removes; whatever edits leaves out is kept, so a view left "
            "out is kept (remove one with null, which also removes its links). Move: "
            '{"views": {"kobi": {"layout": {"position": [330, 0]}}}}. edits may name views, '
            "links, root_view and project.label. document is the whole canvas document, "
            "only for rewriting most of the canvas. Either way the whole result is "
            "validated (every problem comes back together with a fix-it, and nothing "
            "changes then), diffed against the running canvas and applied as one undo "
            "step. label: one line saying what changed; leave it out for a plain move, "
            "resize or relabel (the app writes it). A new view or link goes in whole "
            "under any placeholder key (new1, new2); the app names views (two syllables, "
            "e.g. kobi) and numbers links (l<n>), and the result's renamed maps placeholder "
            "-> id; names never change. If the user changed a field of a view or link while "
            "you were working, a submit that changes the same field is refused with its "
            "current entry: keep the user's value (leave the field out of edits; copy the live "
            "value into a whole document), or ask the user. A view's label is a brief summary "
            "of a few words. "
            "Every view must be reachable by links from the project source, the fixed "
            "node 'project' whose one output is project.all_files (it cannot be moved, "
            "resized, deleted or locked). A view fed from the file tree takes an intaker "
            "with scope 'selection' by default (source files.selected_files: it shows "
            "whatever the user picks); use scope 'pinned' (source project.all_files, fixed "
            "sources) only when the user names specific data, and say which in the link "
            "summary. A view with layout.floating floats fixed in the window above the "
            "canvas (describe canvas: float); one the user floated (by: user) may be "
            "unfloated but not moved or resized. layout.collapsed: true shows a view as "
            "its title bar alone (links keep flowing, no quota); collapse views the user "
            "does not need to see right now.",
            params={
                "type": "object",
                "properties": {
                    "edits": {
                        "type": ["object", "string"],
                        "description": "Only what changes, in the document's shape (JSON "
                        "Merge Patch): null removes, what is left out is kept. JSON object "
                        "or JSON text.",
                    },
                    "document": {
                        "type": ["object", "string"],
                        "description": "The whole canvas document, as a JSON object or JSON "
                        "text; only for rewriting most of the canvas.",
                    },
                    "label": {
                        "type": "string",
                        "description": "One line saying what changed (the user reads it); "
                        "left out, the app writes one from the change.",
                    },
                },
            },
        ),
        ToolSpec(
            "snap",
            "Snapshot of one view (by name, 'kobi') or the whole canvas to check the canvas "
            "you wrote "
            "against: the document entry, the links in and out with their resolve state, health, "
            "the on-screen rect and the host's state; image=true also renders a PNG of it "
            "in the running app (its path comes back as image).",
            read_only=True,
            params={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "a view name such as 'kobi', or 'canvas'",
                    },
                    "image": {
                        "type": "boolean",
                        "description": "also render a PNG (needs the app window)",
                    },
                },
            },
        ),
        ToolSpec(
            "undo",
            "Undo the newest submit, whoever made it (one step).",
            destructive=True,
        ),
    )
}

AGENT_TOOL_NAMES: tuple[str, ...] = tuple(AGENT_TOOLS)


class AgentTools:
    def __init__(self, call: CallFn) -> None:
        self._call = call

    def run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Dispatch by tool name with the tool's JSON arguments (what adapters call).
        ``KeyError`` for a name outside the six."""
        AGENT_TOOLS[name]
        return getattr(self, name)(**args)

    def state(self) -> dict[str, Any]:
        return self._call("canvas.state", {})

    def describe(self, target: str) -> dict[str, Any]:
        return self._call("describe", {"target": target})

    def schema(self) -> dict[str, Any]:
        return self._call("describe", {"target": "canvas"})["info"]

    def submit_canvas(
        self,
        document: dict[str, Any] | str | None = None,
        label: str = "",
        edits: dict[str, Any] | str | None = None,
        since: int | None = None,
    ) -> dict[str, Any]:
        """``since``: the prompt bar's turn start (not in the tool's schema; the turn's
        tools add it), so a change the user made mid-turn is not reverted (#98)."""
        if (document is None) == (edits is None):
            raise InvalidArgs(
                "submit_canvas takes "
                + ("both edits and a document" if edits else "neither edits nor a document"),
                fix="pass exactly one: edits (only what changes; preferred) or document (the "
                "whole canvas document)",
            )
        which = {"edits": edits} if edits is not None else {"document": document}
        if since is not None:
            which["since"] = since
        return self._call("canvas.submit", {**which, "label": label})

    def snap(self, target: str = "canvas", image: bool = False) -> dict[str, Any]:
        """The app's ``snap`` command (structured state plus, on request, the image);
        a core without the app's commands answers from the document alone."""
        try:
            return self._call("snap", {"target": target or "canvas", "image": bool(image)})
        except SpectoError as e:
            if e.code != "unknown_command":  # over the SDK it arrives as a RemoteError
                raise
        doc = self.state()["document"]
        target = (target or "canvas").removeprefix("view:")
        links = doc["links"]
        if target == "canvas":
            meta = {
                "address": "canvas:canvas",
                "label": doc.get("project", {}).get("label", ""),
                "window_frame": doc["window_frame"]["rect"],
                "root_view": doc["root_view"],
                "views": {
                    vid: {"type": v["type"], "label": v["label"], "layout": v["layout"]}
                    for vid, v in doc["views"].items()
                },
                "links": {lid: _link_brief(link) for lid, link in links.items()},
            }
            return {"target": "canvas", "kind": "canvas", "meta": meta, "image": None}
        view = doc["views"].get(target)
        if view is None:
            raise NotFound(
                f"no view {target!r} on the canvas",
                fix=f"view names: {', '.join(doc['views']) or 'none yet'}",
            )
        meta = {
            "id": target,
            "address": f"canvas:canvas/view:{target}",
            **{k: view.get(k) for k in ("type", "label", "layout", "params", "locked")},
            "links": {
                "in": [
                    {"id": lid, **_link_brief(ln)}
                    for lid, ln in links.items()
                    if ln["target"]["view"] == target
                ],
                "out": [
                    {"id": lid, **_link_brief(ln)}
                    for lid, ln in links.items()
                    if ln["source"]["view"] == target
                ],
            },
        }
        return {"target": target, "kind": "view", "meta": meta, "image": None}

    def undo(self) -> dict[str, Any]:
        return self._call("canvas.undo", {})


def _link_brief(link: dict[str, Any]) -> dict[str, Any]:
    """The same brief the app's ``snap`` gives a link (minus its runtime status)."""
    return {
        "kind": link["kind"],
        "source": f"{link['source']['view']}.{link['source']['port']}",
        "target": f"{link['target']['view']}.{link['target']['port']}",
        "summary": link["summary"]["sentence"],
    }
