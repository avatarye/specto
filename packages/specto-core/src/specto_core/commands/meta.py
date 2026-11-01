"""Discovery: list_commands, describe, api.info, vocabulary (HLD section 6, step 1)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ..canvas_document import canvas_json_schema, describe_link_kind, describe_port_type
from ..canvas_example import worked_example
from ..errors import NotFound
from ..kinds import DATA_KINDS
from ..machine import usd_renderer
from ..ports import LINK_KINDS, PORT_TYPES, VIEW_PORTS, VIEW_ROLES
from ..registry import Args, CommandContext, Result, command

#: ``describe canvas`` says how views are named (ADR 0018), next to the schema
NAMES_NOTE = (
    "A view's id is its name: two syllables handed out by the app (kobi, tamu), never "
    "changed; the file tree has one too (find it by type file_tree). Add a new view or "
    "link under any placeholder key (new1, new2): the submit names the view and numbers the "
    "link (l<n>), and the result's 'renamed' maps placeholder -> id. "
    "Call views by name; keep a view's label to a few words (a brief summary)."
)


#: ``describe canvas`` says how the float layer works (#112, ADR 0021)
FLOAT_NOTE = (
    "Two layers: the canvas pans under the window; the float layer stays fixed in the "
    "window above it. A view floats when its layout has floating: {corner, offset, by}: "
    "corner is the window corner it keeps its offset from (top_left, top_right, "
    "bottom_left, bottom_right), offset the px from that corner to the view's matching "
    "corner, inward, so it stays put when the window resizes; size still counts. A "
    "floating view is always on screen: it never pauses and always counts in the quotas. "
    "You may float a view (by: agent, the default) and unfloat one (drop floating; it "
    "lands at layout.position, so set that to the canvas point you want: the one under "
    "it is window_frame.rect x, y plus its window position). A view the user floated "
    "(by: user) is theirs: you may unfloat it but not change its corner, offset or size; "
    "never write by: user yourself. Float views the user should keep in sight while "
    "panning (a legend, a player, the main viewer)."
)


class ListCommandsArgs(Args):
    prefix: str | None = Field(default=None, description="Only names starting with this.")


class ListCommandsResult(Result):
    commands: list[dict[str, Any]]


@command(
    "list_commands", doc="Names and one-line docs of every command.", read_only=True, scope="global"
)
def list_commands(ctx: CommandContext, args: ListCommandsArgs) -> ListCommandsResult:
    items = ctx.engine.registry.list_commands()
    if args.prefix:
        items = [c for c in items if c["name"].startswith(args.prefix)]
    return ListCommandsResult(commands=items)


class DescribeArgs(Args):
    target: str = Field(
        description="A command name, 'canvas' (the canvas document schema), 'view:<type>', "
        "'link:<kind>', 'port:<type>' or 'kind:<data kind>'."
    )


class DescribeResult(Result):
    target: str
    kind: str
    info: dict[str, Any]


@command(
    "describe",
    doc="Describe a command, the canvas document, a view type (with its ports), a link "
    "kind, a port type or a data kind, with schema and an example.",
    read_only=True,
    scope="global",
)
def describe(ctx: CommandContext, args: DescribeArgs) -> DescribeResult:
    t = args.target
    if t == "canvas":
        return DescribeResult(
            target=t,
            kind="canvas",
            info={
                "schema": canvas_json_schema(),
                "example": worked_example(),
                "names": NAMES_NOTE,
                "float": FLOAT_NOTE,
            },
        )
    if t.startswith("link:"):
        if t[5:] not in LINK_KINDS:
            raise NotFound(f"no link kind {t[5:]!r}", fix=f"link kinds: {', '.join(LINK_KINDS)}")
        return DescribeResult(target=t, kind="link", info=describe_link_kind(t[5:]))
    if t.startswith("port:"):
        if t[5:] not in PORT_TYPES:
            raise NotFound(f"no port type {t[5:]!r}", fix=f"port types: {', '.join(PORT_TYPES)}")
        return DescribeResult(target=t, kind="port", info=describe_port_type(t[5:]))
    if t.startswith("view:"):
        m = ctx.engine.plugins.view(t[5:])
        ports = VIEW_PORTS.get(t[5:])
        if m is None and ports is None:
            known = sorted({v.type for v in ctx.engine.plugins.views()} | set(VIEW_PORTS))
            raise NotFound(f"no view type {t[5:]!r}", fix=f"view types: {', '.join(known)}")
        info = {"type": t[5:], "role": VIEW_ROLES.get(t[5:])}
        info |= m.model_dump() if m is not None else {}
        if ports is not None:
            info["ports"] = ports.model_dump()
        if m is not None and m.uses:
            caps = ctx.engine.capabilities
            info["machine"] = {n: caps[n].model_dump() for n in m.uses if n in caps}
        if t[5:] == "usd":
            info["renderer_auto"] = usd_renderer({}, ctx.engine.capabilities)
        return DescribeResult(target=t, kind="view", info=info)
    if t.startswith("kind:"):
        k = t[5:]
        if k not in DATA_KINDS:
            raise NotFound(f"no data kind {k!r}", fix=f"kinds: {', '.join(DATA_KINDS)}")
        views = [v.type for v in ctx.engine.plugins.views() if k in v.accepts]
        locs = [lo.name for lo in ctx.engine.plugins.locators() if k in lo.kinds]
        return DescribeResult(
            target=t, kind="kind", info={"kind": k, "accepted_by": views, "produced_by": locs}
        )
    return DescribeResult(target=t, kind="command", info=ctx.engine.registry.describe(t))


class ApiInfoArgs(Args):
    pass


class ApiInfoResult(Result):
    api_level: int
    api_compatible: int
    schema_version: int
    canvas_schema: dict[str, Any]
    effects: list[str]
    commands: list[dict[str, Any]]
    data_kinds: list[str]
    port_types: list[dict[str, Any]]
    link_kinds: list[dict[str, Any]]
    view_types: list[dict[str, Any]]
    theme_tokens: dict[str, Any]


@command(
    "api.info",
    doc="The api-info.json document: commands, the canvas document schema, data kinds, "
    "port types, link kinds, view types.",
    read_only=True,
    scope="global",
)
def api_info(ctx: CommandContext, args: ApiInfoArgs) -> ApiInfoResult:
    return ApiInfoResult(**ctx.engine.api_info())


class VocabularyArgs(Args):
    pass


class VocabularyResult(Result):
    view_types: list[str]
    port_types: list[str]
    link_kinds: list[str]
    data_kinds: list[str]
    commands: list[str]


@command(
    "vocabulary",
    doc="The short vocabulary: names only, for a first look.",
    read_only=True,
    scope="global",
)
def vocabulary(ctx: CommandContext, args: VocabularyArgs) -> VocabularyResult:
    return VocabularyResult(
        view_types=[v.type for v in ctx.engine.plugins.views()],
        port_types=list(PORT_TYPES),
        link_kinds=list(LINK_KINDS),
        data_kinds=list(DATA_KINDS),
        commands=ctx.engine.registry.names(),
    )
