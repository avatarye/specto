"""Commands the app registers (contracts section 4, "the app's own GUI-only commands").

- ``snap``: structured snapshot of a view or the canvas, with an optional PNG.
- ``shell.locate``: where a named shell item is on screen (scripted GUI tests).
- ``message``: a request from another agent of the user's fleet (#187), shown in the
  agent thread and run as a turn labelled with the sender (``PromptBar.receive_peer``):
  verified when the caller's nimitz member labels name the sender, else claimed (#190).

Importing this module registers them into the default registry, and imports
``specto_intake`` so the running app serves the ``intake.*`` commands too. Qt-free.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ConfigDict, Field

import specto_intake as _intake  # noqa: F401  (registers intake.* commands)
from specto_core.errors import SpectoError
from specto_core.registry import Args, CommandContext, Result, command, registry

from .snap import build_snapshot, default_snap_path, layout_text, resolve_target


# -- snap -----------------------------------------------------------------------------
class SnapArgs(Args):
    target: str = Field(
        default="canvas", description="A view id, 'view:<id>', '@<label>' or 'canvas'."
    )
    image: bool = Field(default=False, description="Also render a PNG (needs the app window).")
    layout: bool = Field(
        default=False,
        description="Also return the layout as text (one line per view and link) to assert on.",
    )
    path: str | None = Field(
        default=None, description="Where to write the PNG; default .specto/snaps/"
    )


class SnapResult(Result):
    target: str
    kind: str
    meta: dict[str, Any]
    image: str | None = None
    image_size: list[int] | None = None
    layout: str | None = None


def snap_provider(ctx: CommandContext) -> Any:
    return getattr(ctx.engine.host, "shell", None)


@command(
    "snap",
    doc="Structured snapshot of a view or the whole canvas (document entry, links in and "
    "out with their resolve state, health, on-screen rect, host state), plus an optional "
    "PNG and layout text from the running app.",
    read_only=True,
    scope="project",
)
def snap(ctx: CommandContext, args: SnapArgs) -> SnapResult:
    oid = resolve_target(ctx.engine, args.target)
    provider = snap_provider(ctx)
    snap_ = build_snapshot(ctx.engine, oid, provider)
    image = size = None
    if args.image:
        if provider is None:
            ctx.report.warning(
                "no app window in this process; metadata only",
                object=oid,
                fix="run snap against a running specto-app",
            )
        else:
            path = Path(args.path) if args.path else default_snap_path(ctx.engine, oid)
            info = provider.grab(oid, path)
            if info.get("error"):
                ctx.report.warning(f"image not rendered: {info['error']}", object=oid)
            else:
                image, size = str(info.get("path")), info.get("size")
    layout = layout_text(ctx.engine, oid, provider) if args.layout else None
    return SnapResult(
        target=oid,
        kind=snap_["kind"],
        meta=snap_["meta"],
        image=image,
        image_size=size,
        layout=layout,
    )


# -- shell.locate ---------------------------------------------------------------------------
class ShellLocateArgs(Args):
    name: str = Field(
        description="objectName of a shell item (slot:kobi, header:kobi, close:kobi, "
        "treeRow:<file tree name>:<path>, projectNode, undoButton, ...)"
    )


class ShellLocateResult(Result):
    name: str
    found: bool
    visible: bool = False
    rect: list[float] | None = Field(default=None, description="[x, y, w, h] in the window.")
    screen: list[float] | None = Field(default=None, description="[x, y, w, h] in screen pixels.")
    shown: list[float] | None = Field(
        default=None,
        description="[x, y, w, h] in the window: the part its clipping ancestors leave in view "
        "(null when none, e.g. a row scrolled out of its list).",
    )
    text: str | None = Field(default=None, description="What a text item says (Text, fields).")


@command(
    "shell.locate",
    doc="Where a named shell item is on screen (window pixel rect), for scripted GUI tests "
    "driving a real mouse; needs the running app. It forces a polish of every item first, "
    "so it answers the item tree as the next frame draws it (a list's removed rows gone).",
    read_only=True,
    scope="project",
)
def shell_locate(ctx: CommandContext, args: ShellLocateArgs) -> ShellLocateResult:
    provider = snap_provider(ctx)
    locate = getattr(provider, "locate", None)
    if provider is None or locate is None:
        ctx.report.warning("no app window in this process", fix="run against a running specto-app")
        return ShellLocateResult(name=args.name, found=False)
    info = locate(args.name)
    if info is None:
        return ShellLocateResult(name=args.name, found=False)
    return ShellLocateResult(name=args.name, found=True, **info)


# -- message (#187) -------------------------------------------------------------------------
class MessageArgs(Args):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    sender: str = Field(
        alias="from",
        description="Who sends it: a fleet path such as FLEET/SQUAD/SHIP (shown in the thread).",
    )
    text: str = Field(description="The request, markdown.")
    reply_to: str | None = Field(
        default=None, description="An id the sender wants quoted back in the reply."
    )
    labels: dict[str, str] | None = Field(
        default=None,
        description="The caller's nimitz member labels from its environment (NIMITZ_ID, "
        "NIMITZ_ROLE). The sender is verified when they name it, by nimitz's own rule; "
        "otherwise it is shown as claimed. A claim grants nothing.",
    )


class MessageResult(Result):
    id: int = Field(description="The peer row's id in the agent thread.")
    mode: str = Field(description="The user's Peer requests setting: auto or ask.")
    status: str = Field(
        description="running (a turn started), queued (after the running turn) or waiting "
        "(the user decides: Run or Dismiss)."
    )
    instance: str = Field(description="This Specto's instance name.")
    verified: bool = Field(
        default=False,
        description="Whether the sender was verified from the caller's nimitz labels "
        "(else it shows as claimed: 'says it is X, unverified').",
    )


@command(
    "message",
    doc="A request from another agent of the user's fleet: it shows in the agent thread as "
    "a peer row and runs as an agent turn labelled with the sender (undoable like any "
    "turn). It is a request, never the user's approval. The user's Peer requests setting "
    "runs it (auto), asks first (ask) or refuses it (off: error code refused).",
    scope="project",
)
def message(ctx: CommandContext, args: MessageArgs) -> MessageResult:
    bar = getattr(getattr(ctx.engine.host, "app", None), "prompt_bar", None)
    if bar is None:
        raise SpectoError(
            "no agent in this process to take the message",
            fix="send it to a running specto-app (`specto instances` lists them)",
        )
    return MessageResult(**bar.receive_peer(args.sender, args.text, args.reply_to, args.labels))


# -- window.raise (#215) --------------------------------------------------------------------
class WindowRaiseArgs(Args):
    pass


class WindowRaiseResult(Result):
    raised: bool = Field(
        description="Whether a window was shown on top (false headless: nothing to raise)."
    )
    instance: str = Field(default="", description="This Specto's instance name.")


@command(
    "window.raise",
    doc="Bring this Specto's window to the front (unminimised, asked to take focus); what "
    "`specto open` does when the project is open already. The system may only flash the "
    "taskbar entry when another app holds the foreground.",
    scope="project",
)
def window_raise(ctx: CommandContext, args: WindowRaiseArgs) -> WindowRaiseResult:
    provider = snap_provider(ctx)
    raise_window = getattr(provider, "raise_window", None)
    app = getattr(ctx.engine.host, "app", None)
    instance = str(getattr(app, "instance_name", "") or "")
    if raise_window is None:
        ctx.report.warning("no app window in this process", fix="run against a running specto-app")
        return WindowRaiseResult(raised=False, instance=instance)
    return WindowRaiseResult(raised=bool(raise_window()), instance=instance)


def ensure_registered() -> None:
    """Idempotent: importing this module registers the commands."""
    assert "snap" in registry and "shell.locate" in registry and "message" in registry
    assert "window.raise" in registry


__all__ = [
    "MessageArgs",
    "ShellLocateArgs",
    "SnapArgs",
    "WindowRaiseArgs",
    "ensure_registered",
    "message",
    "shell_locate",
    "snap",
    "window_raise",
]
