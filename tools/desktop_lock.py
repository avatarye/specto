"""One GUI test at a time on the shared desktop.

Several agents drive real mouse/keyboard tests (pyautogui) on the same Windows desktop;
two at once click into each other's windows. Hold this lock for the whole GUI session.

    uv run python tools/desktop_lock.py run <name> -- <command ...>   # preferred
    uv run python tools/desktop_lock.py acquire <name> [--wait 3600]
    uv run python tools/desktop_lock.py release <name>
    uv run python tools/desktop_lock.py status

The lock is a file in the temp dir holding {name, pid, since, beat}. A holder that
stopped refreshing it for STALE_S (default 20 min; `run` refreshes every 30 s) is
considered dead and the lock is taken over.

The tools that drive the desktop (gui_session.py, gui_drive.py) hold it themselves
(``held``, SPEC-7): under ``run`` they use its lock (``run`` names it to its command in
SPECTO_DESKTOP_LOCK_NAME), otherwise they take it and free it when they end, and they
refuse to drive a desktop someone else holds. A private display (nested_display.py sets
SPECTO_PRIVATE_DISPLAY) needs no lock.
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
from pathlib import Path

LOCK = Path(
    os.environ.get("SPECTO_DESKTOP_LOCK", Path(tempfile.gettempdir()) / "specto-desktop.lock")
)
STALE_S = float(os.environ.get("SPECTO_DESKTOP_LOCK_STALE", "1200"))
#: set by ``run`` for its command: the name the lock is held under
ENV_NAME = "SPECTO_DESKTOP_LOCK_NAME"
#: set by nested_display.py: the command drives a display of its own, not the desktop
ENV_PRIVATE = "SPECTO_PRIVATE_DISPLAY"


def _read() -> dict | None:
    try:
        return json.loads(LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(info: dict) -> None:
    LOCK.write_text(json.dumps(info), encoding="utf-8")


def acquire(name: str, wait_s: float = 3600.0) -> bool:
    deadline = time.monotonic() + wait_s
    announced = False
    while True:
        now = time.time()
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            _write({"name": name, "pid": os.getpid(), "since": now, "beat": now})
            print(f"[OK] desktop lock acquired by {name}")
            return True
        except FileExistsError:
            info = _read() or {}
            if info.get("name") == name:
                _write({**info, "beat": now})
                print(f"[OK] desktop lock already held by {name}")
                return True
            if now - float(info.get("beat", 0)) > STALE_S:
                print(f"[WARN] taking over stale desktop lock from {info.get('name')}")
                LOCK.unlink(missing_ok=True)
                continue
            if not announced:
                print(f"[..] desktop busy ({info.get('name')}); waiting")
                announced = True
        if time.monotonic() >= deadline:
            print("[ERROR] timed out waiting for the desktop lock")
            return False
        time.sleep(5)


def release(name: str) -> None:
    info = _read()
    if info and info.get("name") == name:
        LOCK.unlink(missing_ok=True)
        print(f"[OK] desktop lock released by {name}")
    else:
        print(f"[WARN] {name} does not hold the desktop lock ({(info or {}).get('name')})")


def _beating(name: str) -> threading.Event:
    """Refresh the lock's beat every 30 s while ``name`` holds it; set the event to stop."""
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(30):
            info = _read()
            if info and info.get("name") == name:
                _write({**info, "beat": time.time()})

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
    """Drive the desktop only while holding its lock (SPEC-7). Under ``run`` (its name in
    ENV_NAME, and the lock still under that name) the run's lock serves and stays the
    run's; otherwise take it as ``name``, waiting up to ``wait_s``, and free it at the
    end. ``SystemExit`` when someone else holds it past the wait. A private display
    (ENV_PRIVATE) needs none."""
    if os.environ.get(ENV_PRIVATE):
        yield
        return
    outer = os.environ.get(ENV_NAME, "")
    if outer and (_read() or {}).get("name") == outer:
        yield
        return
    if not acquire(name, wait_s):
        raise SystemExit(
            f"[ERROR] the desktop lock is held by {(_read() or {}).get('name')}: another GUI "
            "session is driving the desktop; wait for it, or run under "
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
        print(json.dumps(_read()))
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
