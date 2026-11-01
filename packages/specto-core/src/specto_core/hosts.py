"""Process-level host: what the app hands the engine so commands can reach the world.

Core never imports Qt. ``snap`` and ``shell.locate`` ask ``engine.host.shell`` (the
app's ``ShellSnapProvider``) for pixel rects and PNGs; a headless engine has none, and
those commands answer with metadata only. ``link.approve`` asks ``engine.host.approvals``
(the app's agent panel) to show the user an approval card (#203, ADR 0006): a headless
engine has none, and the command fails with a fix-it; nothing on this path approves.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AppHost(Protocol):
    """Process-level services only the app has."""

    #: the shell's snap provider (``specto_app.snap.SnapProvider``), ``None`` headless
    shell: Any
    #: where ``link.approve`` requests go: an object with ``ask_approval(link, path,
    #: hash, target, requester, verified) -> int`` (the card's id) that shows the user
    #: the approval card; ``None`` headless (no one to ask)
    approvals: Any


class HeadlessHost:
    """Default host: no window, no snap provider, no one to ask for approval."""

    shell: Any = None
    approvals: Any = None
