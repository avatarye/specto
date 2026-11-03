"""Port vocabulary (ADR 0014): port types, the ports each view type declares, the project
source, and what each link kind maps.

Ports are declared here per view type and never written in the canvas document; agents
read them through ``describe view:<type>`` and the canvas JSON Schema's descriptions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: The v1 port types (spec #35 Implementation Decisions).
PortType = Literal["files", "collection", "item", "selection", "time", "content", "camera"]
PORT_TYPES: tuple[str, ...] = (
    "files",
    "collection",
    "item",
    "selection",
    "time",
    "content",
    "camera",
)

#: Port types on which a link may be two-way (ADR 0014: time and selection only).
TWO_WAY_PORT_TYPES: frozenset[str] = frozenset({"time", "selection"})

ViewType = Literal[
    "file_tree",
    "gallery",
    "image",
    "video",
    "json",
    "markdown",
    "map",
    "3d",
    "html",
    "pdf",
    "preview",
    "usd",
    "scratch",
    "text",
]
VIEW_TYPES: tuple[str, ...] = (
    "file_tree",
    "gallery",
    "image",
    "video",
    "json",
    "markdown",
    "map",
    "3d",
    "html",
    "pdf",
    "preview",
    "usd",
    "scratch",
    "text",
)
#: the context viewer (ADR 0020, #43): the one view type that follows the file tree's
#: picks by design; a dedicated view is never one
CONTEXT_VIEWER = "preview"

#: What a view does, not what data it shows (ADR 0020): a browser shows many items of a
#: collection and outputs a selection; a viewer shows the one selected item in its native
#: form; a player is a viewer whose item has a timeline; a scene places many items in one
#: space. A browser's selection feeds a viewer or player.
ViewRole = Literal["browser", "viewer", "player", "scene"]

#: Every view type declares exactly one role.
VIEW_ROLES: dict[str, ViewRole] = {
    "file_tree": "browser",
    "gallery": "browser",
    "image": "viewer",
    "video": "player",
    "json": "viewer",
    "markdown": "viewer",
    "map": "scene",
    "3d": "scene",
    "html": "viewer",
    "pdf": "viewer",
    "preview": "viewer",
    "usd": "player",
    # the scratch pad (#184) shows many items and outputs a selection (and its items)
    "scratch": "browser",
    # a short text the document carries (#43: "Not supported: <name> (<ext>)")
    "text": "viewer",
}

#: The roles as the agent reads them (the canvas schema's view type, ADR 0020).
VIEW_ROLES_NOTE = (
    "A browser shows many items of a collection and outputs a selection; a viewer shows "
    "the one selected item in its native form; a player is a viewer whose item has a "
    "timeline; a scene places many items in one space. A browser's selection feeds a viewer "
    "or player: the gallery browses, the image view or video shows what it picks."
)

#: View types with a playback timeline (glossary: player); only these may be the root view.
PLAYER_TYPES: frozenset[str] = frozenset(t for t, r in VIEW_ROLES.items() if r == "player")

#: Reserved endpoint id of the project source (glossary): the project folder, drawn as a
#: fixed node at the canvas origin, the one link source that is not a view. Its one output
#: ``all_files`` feeds the file tree's intaker and every pinned intaker (#83).
PROJECT_ID = "project"
PROJECT_PORT = "all_files"
#: where the project node sits on the canvas (x, y, w, h in canvas px): fixed at the
#: origin, 7 px down so its centre (33) lies on the line of the fresh file tree's input
#: port (the tree at y 0: the app's 2 px gutter and 31 px port inset, #121) and the wire
#: between them runs straight; no view may cover it
PROJECT_NODE_RECT: tuple[float, float, float, float] = (0.0, 7.0, 52.0, 52.0)


class PortDecl(BaseModel):
    """The ports a view type exposes. ``propagates`` says which input ports, when set from
    outside, change which output ports of the same view; the cycle check follows those
    edges as well as the links."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    inputs: dict[str, PortType] = Field(default_factory=dict)
    outputs: dict[str, PortType] = Field(default_factory=dict)
    propagates: dict[str, list[str]] = Field(default_factory=dict)


VIEW_PORTS: dict[str, PortDecl] = {
    # the files the user picks go out; all files come from the project source, not here.
    # A selection set from outside (keys: relative paths of files or folders, #168) is
    # selected, revealed and scrolled to, and its files go out on selected_files like a
    # pick; the tree has no selection output, so it is never republished as one
    "file_tree": PortDecl(
        inputs={"data": "collection", "selection": "selection"},
        outputs={"selected_files": "files"},
        propagates={"data": ["selected_files"], "selection": ["selected_files"]},
    ),
    "gallery": PortDecl(
        inputs={"data": "collection", "selection": "selection", "time": "time"},
        outputs={"selection": "selection", "time": "time"},
        # a selection set from outside is republished (two-way sync); a time input only
        # moves the highlight and does not change what the user picked
        propagates={"selection": ["selection"]},
    ),
    "image": PortDecl(
        inputs={"data": "collection", "selection": "selection"},
        outputs={"selection": "selection"},
        propagates={"selection": ["selection"]},
    ),
    "video": PortDecl(
        inputs={"data": "collection", "time": "time"},
        outputs={"time": "time", "selection": "selection"},
        propagates={"time": ["time"]},  # a seek from outside is republished as playback time
    ),
    # the content shown; a selection out when the user picks inside the view (a note
    # list or wikilink in markdown): a selection set from outside is shown, not republished.
    # json also shows a content value linked in (a usd view's selected prim, #140), not
    # republished either
    "json": PortDecl(
        inputs={"data": "collection", "selection": "selection", "content": "content"},
        outputs={"content": "content", "selection": "selection"},
        propagates={"data": ["content"], "selection": ["content"]},
    ),
    "markdown": PortDecl(
        inputs={"data": "collection", "selection": "selection"},
        outputs={"content": "content", "selection": "selection"},
        propagates={"data": ["content"], "selection": ["content"]},
    ),
    "map": PortDecl(
        inputs={"features": "content", "selection": "selection", "time": "time"},
        outputs={"selection": "selection"},
        propagates={"selection": ["selection"]},
    ),
    "3d": PortDecl(
        inputs={"data": "collection", "camera": "camera", "selection": "selection"},
        outputs={"camera": "camera", "selection": "selection"},
        propagates={"camera": ["camera"], "selection": ["selection"]},
    ),
    # the html file shown (picked in the page's file list) goes out as the selection; a
    # selection set from outside is shown, not republished
    "html": PortDecl(
        inputs={"data": "collection", "selection": "selection"},
        outputs={"selection": "selection"},
    ),
    # the pdf shown is the selected item of its collection (or the first); pages, zoom
    # and fit are the view's own, nothing goes out
    "pdf": PortDecl(inputs={"data": "collection", "selection": "selection"}),
    # the context viewer (ADR 0020): the picked item (or the first) in the renderer of its
    # kind; a pick made inside it (a step, a wikilink) goes out, a selection set from
    # outside is shown, not republished; no time port (players keep time)
    "preview": PortDecl(
        inputs={"data": "collection", "selection": "selection"},
        outputs={"selection": "selection"},
    ),
    # one OpenUSD stage, read-only (#132): the picked item (or the first) of its
    # collection; selection keys are ``<item key>#<prim path>``; time is the stage's
    # timecodes in seconds; camera is the 3d view's CameraValue, so the two can share a
    # viewpoint; content is the selected prim's resolved properties
    "usd": PortDecl(
        inputs={
            "data": "collection",
            "selection": "selection",
            "time": "time",
            "camera": "camera",
        },
        outputs={
            "selection": "selection",
            "time": "time",
            "camera": "camera",
            "content": "content",
        },
        propagates={
            "data": ["selection", "content"],
            "selection": ["selection", "content"],
            "time": ["time"],
            "camera": ["camera"],
        },
    ),
    # the project's scratch pad (#184): its items (texts, images, file references kept in
    # .specto/scratch) go out as a collection, the one the user clicks as the selection;
    # a selection set from outside is shown, not republished
    "scratch": PortDecl(
        inputs={"selection": "selection"},
        outputs={"items": "collection", "selection": "selection"},
    ),
    # a short text the document carries in params.text (#43): the "Not supported" view a
    # file no view type shows opens in. Its data input takes the pinned link that says
    # which file it stands for; nothing is read from it and nothing goes out
    "text": PortDecl(inputs={"data": "collection"}),
}


def file_input_of(view_type: str) -> tuple[str, str] | None:
    """The port a view type takes files on and the link kind that feeds it from a files
    port: its first ``collection`` input by an ``intaker`` (most views, on ``data``), else
    its first ``content`` input by ``read_contents`` (the map's ``features``); None for
    a view type with neither (the file tree's own feed is the project intaker)."""
    decl = VIEW_PORTS.get(view_type)
    if decl is None:
        return None
    for want, kind in (("collection", "intaker"), ("content", "read_contents")):
        for port, ptype in decl.inputs.items():
            if ptype == want:
                return port, kind
    return None


#: view types a canvas holds at most one of (#184: the scratch pad is the project's)
SINGLE_TYPES: frozenset[str] = frozenset({"scratch"})
#: view types the project source reaches without a link: they are sources of their own
#: (the scratch pad's items live in the project's .specto folder, #184)
SOURCE_TYPES: frozenset[str] = frozenset({"scratch"})

PROJECT_PORTS = PortDecl(outputs={PROJECT_PORT: "files"})

LinkKind = Literal["intaker", "follow", "read_contents", "time_map", "python"]
LINK_KINDS: tuple[str, ...] = ("intaker", "follow", "read_contents", "time_map", "python")

#: Built-in mapping kinds: (source port type, target port type). ``None`` means "any, but
#: equal on both sides" (an identity mapping). ``python`` declares its output itself.
BUILTIN_KINDS: dict[str, tuple[str | None, str | None]] = {
    "intaker": ("files", "collection"),
    "follow": (None, None),
    "read_contents": ("files", "content"),
    "time_map": ("time", "time"),
}
