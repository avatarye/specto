"""The Specto MCP server: the six agent tools (ADR 0017) over the SDK, for Claude Code
and any other MCP client. The prompt bar's in-process agent gets the same six from
``specto_core.agent_tools``; the names, docs and semantics come from that one table.

Each MCP session is one SDK connection with ``origin="agent:<session>"``; a submit is
one undo step on the project's one canvas, whoever made it.

Two more tools reach the other running Specto apps on this machine (#187): ``instances``
lists them (``specto_core.instances``) and ``message`` sends a request to one's agent,
which shows it in its thread and runs it as a turn labelled with the sender.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from specto_core.agent_tools import AGENT_TOOLS, AgentTools, ToolSpec
from specto_core.errors import SpectoError
from specto_core.instances import (
    Instance,
    caller_labels,
    default_sender,
    find_instance,
    list_instances,
)

from .sessions import AgentSessions

INSTRUCTIONS = """Specto shows a data folder (the project) on one canvas of linked views
(file tree, galleries, image, video, JSON, markdown, map, 3D). You compose the canvas by
submitting changes to its canvas document. Start with `state` (the current document
and undo state) and `schema` (the document's JSON Schema and a worked example);
`describe view:<type>` lists a view type's params and ports, `describe link:<kind>` a link
kind's params. `submit_canvas` takes `edits`, only what changes in the document's own shape
(a JSON Merge Patch: null removes, what is left out is kept), or the whole `document`, with
a one-line label saying what changed: the result is validated (every problem comes back
with a fix-it, nothing changes then), diffed against the running canvas and applied as one
undo step. Check with
`snap`; `undo` reverts the newest submit. Views with `locked: true` refuse moves and
deletions; ask the user.

For a USD stage (.usd, .usda, .usdc, .usdz) use one usd view: a player for one stage at a
time, read-only, fed the file tree's selected_files by a selection intaker with screener
["@usd"]. Its time follows the global clock (make it the root view, or time_map a video
onto it). Params: renderer auto|rtx|storm (auto is rtx on Windows and Linux where `describe
view:usd` shows machine.ovrtx present, else storm; on macOS always storm; `describe view:usd`
reports renderer_auto), camera_speed, camera_mode (free, Unreal Editor's, or orbit: drag
turns around the selection), look_through (a camera prim path, or null), view_mode
(lit|unlit|wireframe|collision; collision shows only the UsdPhysics colliders),
environment (stage|sky|hdri: the stage's own lights, one of NVIDIA's dynamic skies named by
sky (fetched on first use; RTX only, Storm shows the HDRI) or one of the 3d view's HDRIs
named by hdri), the sky's time_of_day (hours), cloudiness and haze (0-1, null: the sky's
own), ground (the sky's floor, off by default) and max_pixels. Set its camera through the
camera port (a follow link from a 3d view's camera) or params, never by running code.
`snap` reports its stage path, renderer, frame size, camera, camera_mode, the environment
that shows, the sky's controls in effect and the sky's fetch. Specto only views USD:
never edit USD files."""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
ADDITIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)


def desc(name: str, arg: str) -> str:
    """The argument's description from the one tool table (``ToolSpec.params``)."""
    return str(AGENT_TOOLS[name].arg(arg).get("description", ""))


def annotations(spec: ToolSpec) -> ToolAnnotations:
    if spec.read_only:
        return READ_ONLY
    return DESTRUCTIVE if spec.destructive else ADDITIVE


def session_key(ctx: Context | None) -> str | None:
    """The transport's session id (streamable HTTP); ``None`` on stdio, where the
    process's default session applies."""
    if ctx is None:
        return None
    try:
        return ctx.request_context.session_id
    except (ValueError, AttributeError):
        return None


def _err(e: SpectoError) -> ToolError:
    """The agent sees code, message (every problem of a rejected submit) and the fix-it."""
    return ToolError(str(e))


#: the tools beside the six agent tools: the running instances and messages to them
FLEET_TOOL_NAMES = ("instances", "message")
#: ``(instance, origin) -> client`` for the instance's endpoint
InstanceConnect = Callable[[Instance, str], Any]


def connect_instance(inst: Instance, origin: str) -> Any:
    from specto_sdk import Client

    return Client.open(inst.project, endpoint=inst.url(), origin=origin, client="specto-mcp")


def build_server(
    sessions: AgentSessions,
    *,
    name: str = "specto",
    connect: InstanceConnect = connect_instance,
) -> MCPServer:
    mcp = MCPServer(name, instructions=INSTRUCTIONS, version="0.2.0")

    def tools(ctx: Context | None) -> AgentTools:
        try:
            st = sessions.get(session_key(ctx))
        except SpectoError as e:
            raise _err(e) from e
        return AgentTools(lambda cmd, args: st.client.call(cmd, args))

    def run(ctx: Context | None, tool: str, **args: Any) -> dict[str, Any]:
        try:
            return tools(ctx).run(tool, args)
        except SpectoError as e:
            raise _err(e) from e

    def tool(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        spec = AGENT_TOOLS[name]
        return mcp.tool(name=name, description=spec.doc, annotations=annotations(spec))

    @tool("state")
    def state(ctx: Context) -> dict[str, Any]:
        return run(ctx, "state")

    @tool("describe")
    def describe(
        ctx: Context,
        target: Annotated[str, Field(description=desc("describe", "target"))],
    ) -> dict[str, Any]:
        return run(ctx, "describe", target=target)

    @tool("schema")
    def schema(ctx: Context) -> dict[str, Any]:
        return run(ctx, "schema")

    @tool("submit_canvas")
    def submit_canvas(
        ctx: Context,
        edits: Annotated[
            dict[str, Any] | str | None, Field(description=desc("submit_canvas", "edits"))
        ] = None,
        document: Annotated[
            dict[str, Any] | str | None, Field(description=desc("submit_canvas", "document"))
        ] = None,
        label: Annotated[str, Field(description=desc("submit_canvas", "label"))] = "",
    ) -> dict[str, Any]:
        return run(ctx, "submit_canvas", edits=edits, document=document, label=label)

    @tool("snap")
    def snap(
        ctx: Context,
        target: Annotated[str, Field(description=desc("snap", "target"))] = "canvas",
        image: Annotated[bool, Field(description=desc("snap", "image"))] = False,
    ) -> dict[str, Any]:
        return run(ctx, "snap", target=target, image=image)

    @tool("undo")
    def undo(ctx: Context) -> dict[str, Any]:
        return run(ctx, "undo")

    @mcp.tool(
        name="instances",
        description="The Specto apps running on this machine: name (5 letters, the address "
        "for message), project folder, project name, pid, endpoint.",
        annotations=READ_ONLY,
    )
    def instances() -> dict[str, Any]:
        return {"instances": [i.model_dump() for i in list_instances()]}

    @mcp.tool(
        name="message",
        description="Send a request to the agent of a running Specto instance (see "
        "instances). It shows in that agent's thread and runs as a turn labelled with the "
        "sender; it is a request, never the user's approval. The user's Peer requests "
        "setting may ask first (status waiting) or refuse it (error refused).",
        annotations=ADDITIVE,
    )
    def message(
        instance: Annotated[str, Field(description="The instance's 5-letter name.")],
        text: Annotated[str, Field(description="The request, markdown.")],
        sender: Annotated[
            str | None,
            Field(description="Who sends it (default: NIMITZ_ID, else user@host)."),
        ] = None,
        reply_to: Annotated[
            str | None, Field(description="An id to be quoted back in the reply.")
        ] = None,
    ) -> dict[str, Any]:
        try:
            inst = find_instance(instance)
            client = connect(inst, "agent:mcp")
            try:
                args: dict[str, Any] = {"from": sender or default_sender(), "text": text}
                if reply_to:
                    args["reply_to"] = reply_to
                # this server runs in the member's session: its labels verify the sender
                labels = caller_labels()
                if labels:
                    args["labels"] = labels
                return client.call("message", args)
            finally:
                client.close()
        except SpectoError as e:
            raise _err(e) from e

    return mcp


def tool_names(server: MCPServer) -> list[str]:
    import anyio

    async def _names() -> list[str]:
        return [t.name for t in await server.list_tools()]

    return anyio.run(_names)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
