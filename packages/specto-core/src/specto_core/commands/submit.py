"""The canvas document commands behind the agent tool set (ADRs 0013, 0017):
canvas.state, canvas.submit, canvas.undo, canvas.redo, canvas.open_file and link.approve. They drive
``engine.live``, the project's one running canvas."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from ..errors import NotFound, SpectoError
from ..instances import sender_verified
from ..live_canvas import SubmitResult
from ..ports import OpenMode
from ..registry import Args, CommandContext, Result, command
from ..txn import UndoResult


class CanvasStateArgs(Args):
    pass


class CanvasStateResult(Result):
    document: dict[str, Any]
    undo: dict[str, Any]
    project: dict[str, Any] | None = None
    links: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Each link's runtime status: state (waiting, resolving, resolved, failed, "
        "needs_approval) and message; a python link also carries path and hash.",
    )


@command(
    "canvas.state",
    doc="The running canvas document and its undo state (cursor, depth, newest submits).",
    read_only=True,
    scope="project",
)
def canvas_state(ctx: CommandContext, args: CanvasStateArgs) -> CanvasStateResult:
    live = ctx.engine.live
    proj = ctx.engine.project
    links = ctx.engine.links
    return CanvasStateResult(
        document=live.document(),
        undo=live.undo_info(),
        project={"name": proj.name, "root": str(proj.root)} if proj else None,
        links={lid: links.status(lid) or {} for lid in live.data["links"]},
    )


class CanvasHistoryArgs(Args):
    last: int | None = Field(default=10, ge=1, description="The newest N submits; None: all.")
    txn: str | None = Field(default=None, description="Only this submit (t<n>).")
    patch: bool = Field(
        default=False,
        description="Also return each submit's patch, inverse and diff (one line per change).",
    )


class CanvasHistoryResult(Result):
    history: list[dict[str, Any]]
    cursor: int
    depth: int


@command(
    "canvas.history",
    doc="The undo history: the newest submits as one line each; with patch=true the detailed "
    "diff of each (spec story 15: only when asked for).",
    read_only=True,
    scope="project",
)
def canvas_history(ctx: CommandContext, args: CanvasHistoryArgs) -> CanvasHistoryResult:
    live = ctx.engine.live
    if args.txn is not None:
        live.record(args.txn)  # NotFound with the fix when it is not in the history
        history = [h for h in live.history_summaries(with_patch=args.patch) if h["txn"] == args.txn]
    else:
        history = live.history_summaries(last=args.last, with_patch=args.patch)
    return CanvasHistoryResult(history=history, cursor=live.cursor, depth=len(live.history))


class CanvasSubmitArgs(Args):
    document: dict[str, Any] | str | None = Field(
        default=None,
        description="The whole canvas document, as a JSON object or as JSON text. Exactly "
        "one of document and edits.",
    )
    edits: dict[str, Any] | str | None = Field(
        default=None,
        description="Only what changes: a JSON Merge Patch (RFC 7396) over the running "
        "document, in its own shape; null removes, what is left out is kept. May name views, "
        "links, root_view and project.label.",
    )
    label: str = Field(
        default="",
        description="One line saying what changed; the user reads it in the reply strip "
        "and the undo history.",
    )
    since: int | None = Field(
        default=None,
        description="A turn start (the prompt bar's LiveCanvas.mark): a change to a view or "
        "link the user changed after it is refused with its live entry (code conflict).",
    )


@command(
    "canvas.submit",
    doc="Submit a whole canvas document or edits (a JSON Merge Patch over the running one): "
    "the result is validated (every problem with a fix-it, nothing changes then), diffed "
    "against the running canvas, applied as one undo step. A new view or link under a "
    "placeholder key is given its name or l<n> id (the result's renamed maps them).",
    scope="project",
)
def canvas_submit(ctx: CommandContext, args: CanvasSubmitArgs) -> SubmitResult:
    return ctx.engine.live.submit(
        args.document, edits=args.edits, origin=ctx.origin, label=args.label, since=args.since
    )


class CanvasOpenFileArgs(Args):
    path: str = Field(description="The file, relative to the project folder (POSIX slashes).")
    mode: OpenMode = Field(
        default="contextual",
        description="contextual (the default, a quick look): the preview, which follows "
        "the file tree's picks, what Ctrl+double-click on a file row opens. dedicated: a "
        "view of the file's own, of the type that shows its kind, fed that one file so "
        "later picks never change it, what a double-click opens.",
    )
    tree: str | None = Field(
        default=None, description="The file tree to open it from; default: the canvas's file tree."
    )
    prefer: list[str] = Field(
        default_factory=list,
        description="Contextual: view ids to reuse first when one of them is a preview "
        "(the shell passes the selected views).",
    )


class CanvasOpenFileResult(Result):
    view: str = Field(description="The view that shows the file.")
    action: str = Field(
        description="Dedicated: created (a new view fed that file) or focused (its "
        "dedicated view was on the canvas already; the shell reveals it). Contextual: "
        "created (new preview on the tree's picks), repointed (an existing preview's "
        "link moved onto the picks) or selected (it already follows them)."
    )
    submit: SubmitResult | None = Field(
        default=None,
        description="The one submit this made; None when the file was picked or its view "
        "was there already.",
    )
    mode: str = Field(
        default="contextual",
        description="How it opened: contextual, or dedicated (asked for, or the fallback "
        "of a contextual open whose kind the preview cannot show; a file no view shows "
        "opens a text view saying Not supported).",
    )
    collapsed: dict[str, str] = Field(
        default_factory=dict,
        description="Views collapsed in the same submit to make room for the new one "
        "under a quota, each with the quota's name (rtx_views: one live RTX view per "
        "canvas, so a second stage's usd view collapses the first).",
    )


@command(
    "canvas.open_file",
    doc="Open a file from the file tree, as the user's double-click does (SPEC-32). mode "
    "contextual (the default; Ctrl+double-click): a quick look in the preview, which "
    "renders the file by its kind; the file is picked in the tree and the preview follows "
    "the tree's picks (a selection intaker), so later picks show in it too; an existing "
    "preview is reused (its link re-pointed at the picks if it had another), else a new one "
    "is placed beside the file tree; a kind the preview cannot show (a usd stage) opens "
    "dedicated instead. mode dedicated (a double-click): a view of the file's own, of the "
    "view type that shows its kind (image, video, markdown, json, pdf, html, 3d, usd, "
    "map), fed by a pinned link from project.all_files to that one file, labelled "
    "'<name> · <folder>'; a file no view shows opens a text view saying 'Not supported: "
    "<name> (<ext>)'; a file whose dedicated view exists is focused, not duplicated. "
    "A new view past a quota collapses the oldest live views in its way (collapsed says "
    "which). Compose typed views with submit_canvas when their ports need linking.",
    scope="project",
)
def canvas_open_file(ctx: CommandContext, args: CanvasOpenFileArgs) -> CanvasOpenFileResult:
    from ..gestures import open_file

    tree = args.tree
    if tree is None:
        views = ctx.engine.live.data["views"]
        tree = next((vid for vid, v in views.items() if v["type"] == "file_tree"), None)
        if tree is None:
            raise NotFound("the canvas has no file tree", fix="submit a file_tree view first")
    opened = open_file(
        ctx.engine.live,
        tree,
        args.path,
        mode=args.mode,
        prefer=args.prefer,
        origin=ctx.origin,
        plugins=ctx.engine.plugins,
    )
    return CanvasOpenFileResult(
        view=opened.view,
        action=opened.action,
        submit=opened.submit,
        mode=opened.mode,
        collapsed=opened.collapsed,
    )


class CanvasUndoArgs(Args):
    pass


@command(
    "canvas.undo",
    doc="Undo the newest submit, whoever made it (one step).",
    scope="project",
    destructive=True,
)
def canvas_undo(ctx: CommandContext, args: CanvasUndoArgs) -> UndoResult:
    return ctx.engine.live.undo(origin=ctx.origin)


@command(
    "canvas.redo",
    doc="Redo the last undone submit.",
    scope="project",
    destructive=True,
)
def canvas_redo(ctx: CommandContext, args: CanvasUndoArgs) -> UndoResult:
    return ctx.engine.live.redo(origin=ctx.origin)


class EventsPollArgs(Args):
    since_seq: int = 0
    limit: int | None = Field(default=None, ge=1)


class EventsPollResult(Result):
    events: list[dict[str, Any]]
    last_seq: int


@command(
    "events.poll",
    doc="The running canvas's events after since_seq (a 'gap' event first if the buffer "
    "rolled past it).",
    read_only=True,
    scope="project",
)
def events_poll(ctx: CommandContext, args: EventsPollArgs) -> EventsPollResult:
    events = ctx.engine.live.events
    evs = events.since(args.since_seq, limit=args.limit)
    return EventsPollResult(events=[e.model_dump() for e in evs], last_seq=events.last_seq)


class LinkApproveArgs(Args):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    link: str = Field(description="The python link whose file needs the user's approval (l<n>).")
    sender: str | None = Field(
        default=None,
        alias="from",
        description="Who asks (shown on the card): a fleet path such as FLEET/SQUAD/SHIP. "
        "Default: the caller's origin and connection.",
    )
    labels: dict[str, str] | None = Field(
        default=None,
        description="The caller's nimitz member labels (NIMITZ_ID, NIMITZ_ROLE): the sender "
        "is verified when they name it, by nimitz's own rule; otherwise the card says "
        "it is only claimed. A claim grants nothing.",
    )


class LinkApproveResult(Result):
    link: str
    path: str
    hash: str = Field(description="sha256 of the file content the card shows.")
    target: dict[str, Any] = Field(description="The link's target {view, port}.")
    status: str = Field(
        description="pending: the window shows the approval card and only the user's click "
        "approves (poll canvas.state or events.poll for the link's state); approved: the "
        "user approved this exact content already, nothing was asked."
    )
    request: int | None = Field(
        default=None, description="The approval card's id in the agent thread (pending)."
    )


@command(
    "link.approve",
    doc="Ask the user to approve the Python file a link references, as its content is "
    "right now (by hash, ADR 0006). This never approves by itself (#203): the window "
    "shows an approval card naming the requester and returns pending; only the user's "
    "click on it (or the view's Approve) records the hash and the link resolves. A "
    "changed file needs approval again. Headless (no window): an error.",
    scope="project",
)
def link_approve(ctx: CommandContext, args: LinkApproveArgs) -> LinkApproveResult:
    links = ctx.engine.links
    req = links.approval_request(args.link)
    if req.pop("approved"):
        if (links.status(args.link) or {}).get("state") == "needs_approval":
            links.resolve(args.link)  # the user approved this content before: let it run
        return LinkApproveResult(**req, status="approved")
    approvals = getattr(ctx.engine.host, "approvals", None)
    if approvals is None:
        raise SpectoError(
            f"no window in this process to ask the user to approve {req['path']}",
            fix="call it on a running specto-app (its endpoint), where the user approves "
            "the file in the view's header or on the card",
        )
    requester = (args.sender or "").strip() or f"{ctx.origin} over {ctx.connection}"
    verified = bool(args.sender) and sender_verified(args.sender, args.labels)
    rid = approvals.ask_approval(**req, requester=requester, verified=verified)
    return LinkApproveResult(**req, status="pending", request=rid)


class LinksRefreshArgs(Args):
    view: str | None = Field(
        default=None,
        description="The view whose intakers rescan (its files stat'ed afresh); omit for the "
        "whole project (every link off the project source).",
    )


class LinksRefreshResult(Result):
    links: list[str] = Field(description="The link ids rescanned.")


@command(
    "links.refresh",
    doc="Rescan now, for views whose auto-update (params.watch) is off: the whole project, or "
    "the intakers feeding one view. One rescan, no undo step; only what changed is delivered.",
    effects=["job"],
    scope="project",
)
def links_refresh(ctx: CommandContext, args: LinksRefreshArgs) -> LinksRefreshResult:
    return LinksRefreshResult(links=ctx.engine.links.refresh(args.view))
