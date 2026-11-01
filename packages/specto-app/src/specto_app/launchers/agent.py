"""The launchers for the prompt-bar agent (#186): ``launchers`` lists what the user
configured (all, or those opening given files) and ``launch`` opens files with one of
them or with the system default. The agent can start nothing else: no command line of
its own, and the system default is refused for programs and scripts. Each launch is a
row in the agent thread. No Qt here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from specto_core.agent_tools import ToolSpec
from specto_core.errors import SpectoError

from .book import SYSTEM, SYSTEM_LABEL, Launched, LauncherBook

_PATHS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Files or folders, relative to the project folder (POSIX slashes) or absolute.",
}

LAUNCH_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "launchers",
            "The external programs the user configured to open files (launchers.toml: "
            "isolated Blender builds and the like): id, label, the file-name globs each "
            "matches, default. With paths: only those opening every one of them, the "
            "default among them, and whether the system default may open them (refused "
            "for programs and scripts).",
            read_only=True,
            params={"type": "object", "properties": {"paths": _PATHS}},
        ),
        ToolSpec(
            "launch",
            "Open files in an external program on the user's desktop, detached: a "
            f"launcher's id (from launchers) or {SYSTEM!r} for the system's default "
            "program. Only when the user asks to open or edit files in a program. A "
            "launcher with each=false gets all the paths in one process; otherwise one "
            "process per file. Nothing comes back but that it started.",
            params={
                "type": "object",
                "properties": {
                    "launcher": {
                        "type": "string",
                        "description": f"A launcher id from launchers, or {SYSTEM!r}.",
                    },
                    "paths": _PATHS,
                },
                "required": ["launcher", "paths"],
            },
        ),
    )
}


class LaunchTools:
    """The two tools over the project's ``LauncherBook``."""

    def __init__(self, book: LauncherBook) -> None:
        self.book = book

    def run(self, tool: str, args: dict[str, Any]) -> tuple[Launched | None, dict[str, Any]]:
        """One call: the launch (``None`` for the listing) and the result the agent reads."""
        if tool == "launchers":
            return None, self.book.summary(args.get("paths") or None)
        if tool != "launch":
            raise KeyError(tool)
        done = self.book.launch(str(args.get("launcher") or "").strip(), args.get("paths") or [])
        root = self.book.root
        shown = [_rel(p, root) for p in done.paths]
        return done, {
            "launched": done.label,
            "launcher": done.launcher,
            "paths": shown,
            "processes": max(1, len(done.pids)),
            "note": done.note,
        }

    def refusal(self, args: dict[str, Any], error: SpectoError) -> Launched:
        """The launch ``args`` asked for that ``error`` stopped, for the thread's row."""
        lid = str(args.get("launcher") or "").strip()
        found = self.book.get(lid) if lid and lid != SYSTEM else None
        label = SYSTEM_LABEL if lid == SYSTEM else found.label if found is not None else lid
        paths = args.get("paths") or []
        if isinstance(paths, str):
            paths = [paths]
        why = error.message.removeprefix("refused: ")
        return Launched(lid, label or "a launcher", [str(p) for p in paths], refused=why)


def _rel(path: str, root: Any) -> str:
    if root is None:
        return path
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return path


__all__ = ["LAUNCH_TOOLS", "LaunchTools"]
