"""Starting what a launcher names (#186): a detached process with no console window and
no handle kept, the system default's opener, Explorer's reveal and the editor for the
launcher file. No Qt here; ``LauncherBook`` takes these as defaults and tests swap them.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from collections.abc import Sequence

#: Windows process creation flags (``subprocess`` has the first three as constants)
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def spawn_detached(argv: Sequence[str], cwd: str = "", env: dict[str, str] | None = None) -> int:
    """Start ``argv`` (a list: no shell reads it) on its own: no console window, no
    pipes, its own process group, out of Specto's job when the job allows, and no handle
    kept, so closing Specto leaves it running. Returns its pid; ``OSError`` when it
    cannot start."""
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    kw: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": cwd or None,
        "env": full_env,
    }
    if sys.platform == "win32":
        flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(
                list(argv), creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw
            )
        except PermissionError:  # the job Specto runs in forbids breaking away
            proc = subprocess.Popen(list(argv), creationflags=flags, **kw)
    else:
        proc = subprocess.Popen(list(argv), start_new_session=True, **kw)
    pid = proc.pid
    _let_go(proc)
    return pid


def _let_go(proc: subprocess.Popen[bytes]) -> None:
    """Drop the process's handle now (Windows) and keep ``Popen.__del__`` from warning
    that it still runs: Specto never waits for a launched program."""
    handle = getattr(proc, "_handle", None)
    if handle is not None:
        with contextlib.suppress(OSError):
            handle.Close()
    proc.returncode = 0


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
            creationflags=CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _let_go(proc)
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
