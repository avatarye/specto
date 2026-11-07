"""Running approved code in a child interpreter that cannot outlive or flood its caller
(ADR 0006's code-running paths, contracts section 10). Qt-free.

- ``NO_WINDOW``: the child gets no console window of its own on Windows (the app has
  none); 0 elsewhere, where ``subprocess`` takes no creation flags.
- :func:`kill_tree` ends the child and everything it started (``taskkill /T`` on
  Windows, its own session's process group elsewhere): a grandchild holding the pipes
  would otherwise keep a run from ever returning, or keep running after it.
- :func:`run_child` feeds one request on stdin and reads stdout and stderr in threads up
  to byte caps; a timeout or a passed cap kills the tree. ``env`` replaces the
  environment (:func:`minimal_env`: only what an interpreter needs to start, so the
  caller's environment never reaches the child's code).
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .detached import CREATE_NO_WINDOW

NO_WINDOW = CREATE_NO_WINDOW if os.name == "nt" else 0
#: the variables a child interpreter keeps under :func:`minimal_env`
MINIMAL_ENV_KEYS = ("SYSTEMROOT", "PATH", "TEMP", "TMP", "TMPDIR")


def minimal_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Only ``MINIMAL_ENV_KEYS`` of ``environ`` (default: this process's)."""
    env = os.environ if environ is None else environ
    keys = {k.upper(): k for k in env}
    return {name: env[keys[name]] for name in MINIMAL_ENV_KEYS if name in keys}


def kill_tree(proc: subprocess.Popen) -> None:
    """End the process and whatever it started (module docstring)."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            creationflags=NO_WINDOW,
        )
    else:  # pragma: no cover - the session was started for this
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, 9)
    with contextlib.suppress(OSError):
        proc.kill()


@dataclass
class ChildRun:
    stdout: bytes
    stderr: bytes
    returncode: int | None
    timed_out: bool = False
    #: "stdout" or "stderr" when that stream passed its cap (the run was killed)
    over_cap: str | None = None


def run_child(
    cmd: list[str],
    request: bytes,
    *,
    timeout: float,
    out_cap: int,
    err_cap: int,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> ChildRun:
    """Run ``cmd`` with ``request`` on stdin (module docstring)."""
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=NO_WINDOW,
        start_new_session=os.name != "nt",
        env=None if env is None else dict(env),
        cwd=cwd,
    )
    out: list[bytes] = []
    err: list[bytes] = []
    over: list[str] = []

    def pump(stream: Any, cap: int, sink: list[bytes], name: str) -> None:
        total = 0
        while chunk := stream.read(65536):
            total += len(chunk)
            if total > cap:
                over.append(name)
                kill_tree(proc)
                break
            sink.append(chunk)

    def feed() -> None:
        with contextlib.suppress(OSError):
            proc.stdin.write(request)
            proc.stdin.close()

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, out_cap, out, "stdout"), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, err_cap, err, "stderr"), daemon=True),
        threading.Thread(target=feed, daemon=True),
    ]
    for t in threads:
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5.0)
    for t in threads:
        t.join(timeout=5.0)
    return ChildRun(
        b"".join(out), b"".join(err), proc.returncode, timed_out, over[0] if over else None
    )


__all__ = [
    "MINIMAL_ENV_KEYS",
    "NO_WINDOW",
    "ChildRun",
    "kill_tree",
    "minimal_env",
    "run_child",
]
