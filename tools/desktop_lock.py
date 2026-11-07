"""One GUI test at a time on the shared desktop.

Several agents drive real mouse/keyboard tests (pyautogui) on the same Windows desktop;
two at once click into each other's windows. Hold this lock for the whole GUI session.

    uv run python tools/desktop_lock.py run <name> -- <command ...>   # preferred
    uv run python tools/desktop_lock.py acquire <name> [--wait 3600]
    uv run python tools/desktop_lock.py release <name>
    uv run python tools/desktop_lock.py status

The lock is a file in the temp dir holding {name, pid, since}; its modification time is
the holder's beat (refreshed every 30 s). It appears whole: a temp file hard-linked into
place, which also fails when someone holds it. It is stale when its holder's pid is gone
or its beat is older than STALE_S (default 20 min); a lock that cannot be read is busy,
never stale, unless unchanged for STALE_S. Nothing acts on a lock it has not just
confirmed: a takeover renames the stale file aside and checks it moved the lock it
judged (else puts it back); a release renames its own aside and checks it is still its
own; a beat only touches a lock that names its holder (a touch never rewrites anyone's).

The tools that drive the desktop (gui_session.py, gui_drive.py) hold it themselves
(``held``, SPEC-7): under ``run`` they use its lock (``run`` names it to its command in
SPECTO_DESKTOP_LOCK_NAME), otherwise they take it and free it when they end, and they
refuse to drive a desktop someone else holds. The tools that wipe a GUI project folder
take it around the wipe. A private display (nested_display.py sets
SPECTO_PRIVATE_DISPLAY) needs no lock.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from gui_procs import pid_alive

LOCK = Path(
    os.environ.get("SPECTO_DESKTOP_LOCK", Path(tempfile.gettempdir()) / "specto-desktop.lock")
)
STALE_S = float(os.environ.get("SPECTO_DESKTOP_LOCK_STALE", "1200"))
#: how often a waiter looks again, and a holder beats
POLL_S = 0.5
BEAT_S = 30.0
#: set by ``run`` for its command: the name the lock is held under
ENV_NAME = "SPECTO_DESKTOP_LOCK_NAME"
#: set by nested_display.py: the command drives a display of its own, not the desktop
ENV_PRIVATE = "SPECTO_PRIVATE_DISPLAY"


@dataclass(frozen=True)
class LockInfo:
    """What the lock file records (its beat is the file's modification time)."""

    name: str
    pid: int
    since: float

    @classmethod
    def parse(cls, text: str | None) -> LockInfo | None:
        """The record, or ``None`` for an unreadable file (being written, or torn)."""
        try:
            raw = json.loads(text or "")
            return cls(name=str(raw["name"]), pid=int(raw["pid"]), since=float(raw["since"]))
        except (ValueError, TypeError, KeyError):
            return None


def _read_text(path: Path | None = None) -> str | None:
    try:
        return (path or LOCK).read_text(encoding="utf-8")
    except OSError:
        return None


def read() -> LockInfo | None:
    return LockInfo.parse(_read_text())


def holder_name() -> str:
    """Who holds the lock, for messages."""
    info = read()
    if info is not None:
        return info.name
    return "nobody" if not LOCK.exists() else "someone (being written)"


def _aside() -> Path:
    return LOCK.with_name(f"{LOCK.name}.{os.getpid()}.{secrets.token_hex(3)}.aside")


def _create(name: str) -> bool:
    """Put a whole lock for ``name`` in place, if there is none: a temp file with the
    record, hard-linked as the lock (the link fails when the lock exists)."""
    tmp = LOCK.with_name(f"{LOCK.name}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    tmp.write_text(json.dumps(asdict(LockInfo(name, os.getpid(), time.time()))), "utf-8")
    try:
        os.link(tmp, LOCK)
        return True
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)


def _put_back(aside: Path) -> None:
    """Return a lock moved aside by mistake, unless someone made a new one meanwhile."""
    try:
        os.link(aside, LOCK)
    except FileExistsError:
        print(f"[WARN] a desktop lock was replaced while moved aside ({aside.name})")
    finally:
        aside.unlink(missing_ok=True)


def _move_aside() -> Path | None:
    aside = _aside()
    try:
        os.replace(LOCK, aside)
    except (FileNotFoundError, PermissionError):  # gone, or a reader has it open now
        return None
    return aside


def stale_reason(info: LockInfo | None, now: float) -> str | None:
    """Why the lock (as read: ``None`` when unreadable) may be taken over; ``None`` while
    it is held."""
    try:
        age = now - LOCK.stat().st_mtime
    except OSError:
        return None
    if info is None:
        return f"unreadable and unchanged for {age:.0f} s" if age > STALE_S else None
    if not pid_alive(info.pid):
        return f"its holder (pid {info.pid}) is gone"
    return f"no beat for {age:.0f} s" if age > STALE_S else None


def take_over(judged: str | None) -> bool:
    """Remove the lock judged stale (its text as judged): rename it aside, and keep it
    removed only if the moved file is that same lock; else put it back. True when the
    stale lock is gone (the caller then creates its own)."""
    aside = _move_aside()
    if aside is None:
        return False
    if _read_text(aside) == judged:
        aside.unlink(missing_ok=True)
        return True
    _put_back(aside)
    return False


def acquire(name: str, wait_s: float = 3600.0) -> bool:
    deadline = time.monotonic() + wait_s
    announced = False
    while True:
        if _create(name):
            print(f"[OK] desktop lock acquired by {name}")
            return True
        text = _read_text()
        info = LockInfo.parse(text)
        if info is not None and info.name == name:
            with contextlib.suppress(OSError):
                os.utime(LOCK)
            print(f"[OK] desktop lock already held by {name}")
            return True
        why = stale_reason(info, time.time())
        if why is not None and take_over(text):
            print(f"[WARN] took over the stale desktop lock of {info.name if info else '?'}: {why}")
            continue
        if not announced:
            print(f"[..] desktop busy ({holder_name()}); waiting")
            announced = True
        if time.monotonic() >= deadline:
            print("[ERROR] timed out waiting for the desktop lock")
            return False
        time.sleep(POLL_S)


def release(name: str) -> None:
    """Free the lock if ``name`` holds it: renamed aside, checked, then removed; a lock
    that is no longer ``name``'s goes back untouched."""
    info = read()
    if info is None or info.name != name:
        print(f"[WARN] {name} does not hold the desktop lock ({holder_name()})")
        return
    aside = _move_aside()
    if aside is None:
        print(f"[WARN] {name} could not release the desktop lock (in use); it goes stale")
        return
    moved = LockInfo.parse(_read_text(aside))
    if moved is not None and moved.name == name:
        aside.unlink(missing_ok=True)
        print(f"[OK] desktop lock released by {name}")
        return
    _put_back(aside)
    print(f"[WARN] {name} no longer held the desktop lock; left it to its holder")


def _beating(name: str) -> threading.Event:
    """Touch the lock every BEAT_S while ``name`` holds it; set the event to stop. A beat
    that fails says so and the next one tries again."""
    stop = threading.Event()

    def beat() -> None:
        lost = False
        while not stop.wait(BEAT_S):
            try:
                info = read()
                if info is not None and info.name == name:
                    os.utime(LOCK)
                    lost = False
                elif not lost:
                    print(f"[WARN] {name} lost the desktop lock ({holder_name()})")
                    lost = True
            except Exception as e:  # a beat must never end silently
                print(f"[WARN] desktop lock beat for {name} failed: {e}; trying again")

    threading.Thread(target=beat, daemon=True).start()
    return stop


def run(name: str, cmd: list[str], wait_s: float) -> int:
    if not acquire(name, wait_s):
        return 3
    stop = _beating(name)
    try:
        return subprocess.call(cmd, env={**os.environ, ENV_NAME: name})
    finally:
        stop.set()
        release(name)


@contextlib.contextmanager
def held(name: str, wait_s: float = 3600.0) -> Iterator[None]:
    """Drive the desktop (or wipe a GUI project folder) only while holding its lock
    (SPEC-7). Under ``run`` (its name in ENV_NAME, and the lock still under that name)
    the run's lock serves and stays the run's; otherwise take it as ``name`` plus a nonce
    (a recycled pid never matches an old lock), waiting up to ``wait_s``, and free it at
    the end. ``SystemExit`` when someone else holds it past the wait. A private display
    (ENV_PRIVATE) needs none."""
    if os.environ.get(ENV_PRIVATE):
        yield
        return
    outer = os.environ.get(ENV_NAME, "")
    current = read()
    if outer and current is not None and current.name == outer:
        yield
        return
    name = f"{name}-{secrets.token_hex(3)}"
    if not acquire(name, wait_s):
        raise SystemExit(
            f"[ERROR] the desktop lock is held by {holder_name()}: another GUI session is "
            "driving the desktop; wait for it, or run under "
            "`tools/desktop_lock.py run <name> -- ...`"
        )
    stop = _beating(name)
    try:
        yield
    finally:
        stop.set()
        release(name)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("op", choices=["acquire", "release", "run", "status"])
    ap.add_argument("name", nargs="?", default="")
    ap.add_argument("--wait", type=float, default=3600.0)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    ns = ap.parse_args(argv)
    if ns.op == "status":
        info = read()
        print(json.dumps(asdict(info) if info else None))
        return 0
    if not ns.name:
        ap.error("name is required")
    if ns.op == "acquire":
        return 0 if acquire(ns.name, ns.wait) else 3
    if ns.op == "release":
        release(ns.name)
        return 0
    cmd = ns.cmd[1:] if ns.cmd[:1] == ["--"] else ns.cmd
    if not cmd:
        ap.error("run needs a command after --")
    return run(ns.name, cmd, ns.wait)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
