"""Starting what a launcher names (#186): a detached process with no console window and
no handle kept, the system default's opener, Explorer's reveal and the editor for the
launcher file. No Qt here; ``LauncherBook`` takes these as defaults and tests swap them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence

from specto_core import detached


def spawn_detached(argv: Sequence[str], cwd: str = "", env: dict[str, str] | None = None) -> int:
    """Start ``argv`` (a list: no shell reads it) on its own: no console window, no
    pipes, its own process group, out of Specto's job when the job allows, and no handle
    kept, so closing Specto leaves it running (``specto_core.detached``). ``env`` adds to
    ours. Returns its pid; ``OSError`` when it cannot start."""
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    return detached.spawn_detached(argv, cwd=cwd or None, env=full_env)


def start_file(path: str) -> None:
    """The system's default program for ``path`` (``os.startfile``; ``open`` / ``xdg-open``
    elsewhere)."""
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        spawn_detached(["open", path])
    else:
        spawn_detached(["xdg-open", path])


def reveal(paths: Sequence[str]) -> None:
    """Show the files in the system's file manager, selected: Explorer for the first
    (``explorer /select,<path>``; Explorer takes one), Finder or the folder elsewhere."""
    path = os.path.normpath(paths[0])
    if sys.platform == "win32":
        # Explorer parses its own command line and wants /select,"<path>" (an argv item
        # with a space would be quoted whole, which it misreads). No shell reads it, and
        # a Windows path cannot hold a quote, so the path cannot break out.
        if '"' in path:
            raise OSError(f"not a Windows path: {path}")
        explorer = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "explorer.exe")
        proc = subprocess.Popen(
            f'"{explorer}" /select,"{path}"',
            creationflags=detached.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        detached.let_go(proc)
        return
    if sys.platform == "darwin":
        spawn_detached(["open", "-R", path])
    else:
        spawn_detached(["xdg-open", os.path.dirname(path)])


def edit_file(path: str) -> None:
    """Open a text file in the system's editor: its association, else Notepad (TOML has
    no association on a fresh Windows)."""
    try:
        start_file(path)
    except OSError:
        if sys.platform != "win32":
            raise
        spawn_detached(["notepad.exe", path])


__all__ = ["edit_file", "reveal", "spawn_detached", "start_file"]
