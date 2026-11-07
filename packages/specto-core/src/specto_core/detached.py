"""Starting a process that outlives Specto and shows no console window (SPEC-28): the one
helper behind ``specto open`` (#215), the app's project switch (``launcher.relaunch``)
and the launchers (#186). Qt-free.

On Windows the child gets a hidden console (``CREATE_NO_WINDOW``), never none
(``DETACHED_PROCESS``): a console child spawned without flags of its own (the Agent SDK's
``claude``, the worker pool's interpreters) inherits a hidden console, but under a
console-less parent each one would open a visible console window. The child gets its own
process group and leaves the caller's job when the job allows (``uv run`` and the tool
shim keep a kill-on-close job). Linux and macOS: ``start_new_session``.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

#: Windows process creation flags (``subprocess`` has them only on Windows)
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
#: what must never be used for a child that starts console programs (see the module doc)
DETACHED_PROCESS = 0x00000008


def spawn_detached(
    argv: Sequence[str],
    *,
    log_path: Path | None = None,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Start ``argv`` (a list: no shell reads it) on its own: stdin closed, stdout and
    stderr appended to ``log_path`` (discarded without one), no console window, its own
    process group, and no handle kept, so the caller never waits for it. ``env`` is the
    child's whole environment (None: ours). Returns the pid; ``OSError`` when it cannot
    start."""
    if log_path is None:
        return _spawn(argv, subprocess.DEVNULL, cwd, env)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        return _spawn(argv, log, cwd, env)


def _spawn(argv: Sequence[str], out: Any, cwd: Path | str | None, env: Any) -> int:
    kw: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": out,
        "stderr": out,
        "close_fds": True,
        "cwd": str(cwd) if cwd else None,
        "env": dict(env) if env is not None else None,
    }
    args = list(argv)
    if sys.platform == "win32":
        flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(args, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw)
        except PermissionError:  # the job we run in forbids breaking away
            proc = subprocess.Popen(args, creationflags=flags, **kw)
    else:
        proc = subprocess.Popen(args, start_new_session=True, **kw)
    pid = proc.pid
    let_go(proc)
    return pid


def let_go(proc: subprocess.Popen[Any]) -> None:
    """Drop the process handle now (Windows) and keep ``Popen.__del__`` from warning that
    the process still runs: nobody waits for it."""
    handle = getattr(proc, "_handle", None)
    if handle is not None:
        with contextlib.suppress(OSError):
            handle.Close()
    proc.returncode = 0


__all__ = [
    "CREATE_BREAKAWAY_FROM_JOB",
    "CREATE_NEW_PROCESS_GROUP",
    "CREATE_NO_WINDOW",
    "DETACHED_PROCESS",
    "let_go",
    "spawn_detached",
]
