"""One GUI test at a time on the shared desktop.

Several agents drive real mouse/keyboard tests (pyautogui) on the same Windows desktop;
two at once click into each other's windows. Hold this lock for the whole GUI session.

    uv run python tools/desktop_lock.py run <name> [--wait 3600] -- <command ...>
    uv run python tools/desktop_lock.py status

The lock is an OS lock on a fixed file in the temp dir (``msvcrt.locking`` on Windows,
``fcntl.flock`` elsewhere), held by the holder process for as long as it runs: the OS
frees it the moment that process ends, however it ends, so there is no staleness, beat
or takeover. A waiter retries the non-blocking lock every POLL_S. The holder's name and
pid go into a sidecar file for messages and ``status`` only; nothing decides on it.
There is no separate acquire or release command: the lock dies with the process that
holds it, so ``run`` holds it around a command.

The tools that drive the desktop (gui_session.py, gui_drive.py) hold it themselves
(``held``, SPEC-7), and so do the tools that wipe a GUI project folder (around the wipe).
Under ``run``, or inside another ``held`` of the same process tree, the lock is already
held (SPECTO_DESKTOP_LOCK_NAME says by whom) and ``held`` passes through. A session
refuses to drive a desktop someone else holds past its wait. A private display
(nested_display.py sets SPECTO_PRIVATE_DISPLAY) needs no lock.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

LOCK = Path(
    os.environ.get("SPECTO_DESKTOP_LOCK", Path(tempfile.gettempdir()) / "specto-desktop.lock")
)
#: how often a waiter tries the lock again
POLL_S = 0.5
#: set while the lock is held, for the holder's children: the name it is held under
LOCK_NAME_ENV = "SPECTO_DESKTOP_LOCK_NAME"
#: set by nested_display.py: the command drives a display of its own, not the desktop
PRIVATE_DISPLAY_ENV = "SPECTO_PRIVATE_DISPLAY"


def sidecar() -> Path:
    """Who holds the lock, for messages only."""
    return LOCK.with_name(LOCK.name + ".holder")


def _try_lock(f: IO[bytes]) -> bool:
    """Lock byte 0 of the open lock file without waiting (the file is never written): the
    same primitive as the board's ``tools/heavy_slot.py``."""
    try:
        f.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(f: IO[bytes]) -> None:
    with contextlib.suppress(OSError):
        f.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    f.close()


def holder_name() -> str:
    """Who holds the lock, as its holder wrote it (display only)."""
    try:
        info = json.loads(sidecar().read_text(encoding="utf-8"))
        return f"{info['name']} (pid {info['pid']})"
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"


def acquire(name: str, wait_s: float = 3600.0) -> IO[bytes] | None:
    """The held lock: an open file to keep open and ``release``; None after ``wait_s``."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_s
    announced = False
    while True:
        f = open(LOCK, "a+b")  # noqa: SIM115  (held open while the lock is held)
        if _try_lock(f):
            with contextlib.suppress(OSError):
                record = {"name": name, "pid": os.getpid(), "since": time.time()}
                sidecar().write_text(json.dumps(record), encoding="utf-8")
            print(f"[OK] desktop lock acquired by {name}")
            return f
        f.close()
        if not announced:
            print(f"[..] desktop busy ({holder_name()}); waiting")
            announced = True
        if time.monotonic() >= deadline:
            print("[ERROR] timed out waiting for the desktop lock")
            return None
        time.sleep(POLL_S)


def release(f: IO[bytes], name: str) -> None:
    with contextlib.suppress(OSError):
        sidecar().unlink(missing_ok=True)
    _unlock(f)
    print(f"[OK] desktop lock released by {name}")


def is_free() -> bool:
    """Whether nobody holds the lock this moment (``status``)."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK, "a+b")  # noqa: SIM115  (closed by _unlock or below)
    if _try_lock(f):
        _unlock(f)
        return True
    f.close()
    return False


@dataclass
class Hold:
    """This process's hold: the open lock file, the name it is held under and how many
    ``held`` blocks (threads) share it."""

    file: IO[bytes] | None = None
    name: str = ""
    count: int = 0


_hold = Hold()
#: guards ``_hold``, and is kept across ``acquire`` so that two threads arriving with no
#: hold never both try the OS lock (the second would wait on its own process)
_hold_lock = threading.Lock()


@contextlib.contextmanager
def held(name: str, wait_s: float = 3600.0) -> Iterator[None]:
    """Drive the desktop (or wipe a GUI project folder) only while holding its lock
    (SPEC-7). Inside ``run`` or a ``held`` of a parent process (LOCK_NAME_ENV set) the
    lock is already held: pass through, but only while the OS lock is in fact taken. A
    name whose holder is gone (the parent ended; the variable leaked into a process that
    is no child of a holder, such as an agent session the app started) decides nothing:
    the lock is taken here. Threads of one process share one hold, freed when the last of
    them leaves; while it is held LOCK_NAME_ENV is set, so children pass through.
    ``SystemExit`` when someone else holds it past ``wait_s``. A private display
    (PRIVATE_DISPLAY_ENV) needs none."""
    if os.environ.get(PRIVATE_DISPLAY_ENV):
        yield
        return
    with _hold_lock:
        parent_holds = not _hold.count and bool(os.environ.get(LOCK_NAME_ENV)) and not is_free()
        if _hold.count:
            _hold.count += 1
        elif not parent_holds:
            f = acquire(name, wait_s)  # under _hold_lock: one taker per process
            if f is None:
                raise SystemExit(
                    f"[ERROR] the desktop lock is held by {holder_name()}: another GUI "
                    "session is driving the desktop; wait for it, or run under "
                    "`tools/desktop_lock.py run <name> -- ...`"
                )
            _hold.file, _hold.name, _hold.count = f, name, 1
            os.environ[LOCK_NAME_ENV] = name
    if parent_holds:
        yield
        return
    try:
        yield
    finally:
        with _hold_lock:
            _hold.count -= 1
            last = _hold.count == 0
            if last:
                f, held_name = _hold.file, _hold.name
                _hold.file, _hold.name = None, ""
                os.environ.pop(LOCK_NAME_ENV, None)
        if last and f is not None:
            release(f, held_name)


def run(name: str, cmd: list[str], wait_s: float) -> int:
    """Hold the lock while ``cmd`` runs; ``cmd`` and its children pass through."""
    with held(name, wait_s):
        return subprocess.call(cmd)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("op", choices=["run", "status"])
    ap.add_argument("name", nargs="?", default="")
    ap.add_argument("--wait", type=float, default=3600.0)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    ns = ap.parse_args(argv)
    if ns.op == "status":
        print("free" if is_free() else f"held by {holder_name()}")
        return 0
    if not ns.name:
        ap.error("name is required")
    cmd = ns.cmd[1:] if ns.cmd[:1] == ["--"] else ns.cmd
    if not cmd:
        ap.error("run needs a command after --")
    try:
        return run(ns.name, cmd, ns.wait)
    except SystemExit as e:
        print(e)
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
