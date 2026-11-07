"""One GUI test at a time on the shared desktop.

Several agents drive real mouse/keyboard tests (pyautogui) on the same Windows desktop;
two at once click into each other's windows. Hold this lock for the whole GUI session.

    uv run python tools/desktop_lock.py run <name> -- <command ...>   # preferred
    uv run python tools/desktop_lock.py acquire <name> [--wait 3600]
    uv run python tools/desktop_lock.py release <name>
    uv run python tools/desktop_lock.py status

The lock is a file in the temp dir holding {name, pid, since, beat}, written whole
(a temp file and ``os.replace``). It is stale, and taken over, when its holder's pid is
gone or it stopped refreshing it for STALE_S (default 20 min; `run` refreshes every
30 s). A lock that cannot be read (a holder mid-write) is busy, never stale, unless its
file has not changed for STALE_S.

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
import secrets
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


def _read_text() -> str | None:
    try:
        return LOCK.read_text(encoding="utf-8")
    except OSError:
        return None


def _parse(text: str | None) -> dict | None:
    try:
        info = json.loads(text or "")
    except ValueError:
        return None
    return info if isinstance(info, dict) else None


def _read() -> dict | None:
    return _parse(_read_text())


def _write(info: dict) -> None:
    """Write the lock whole: a reader sees the old content or the new, never half."""
    tmp = LOCK.with_name(f"{LOCK.name}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    tmp.write_text(json.dumps(info), encoding="utf-8")
    for _ in range(40):
        try:
            os.replace(tmp, LOCK)
            return
        except PermissionError:  # Windows: a reader has it open this instant
            time.sleep(0.05)
    tmp.unlink(missing_ok=True)
    raise PermissionError(f"cannot write {LOCK}")


def pid_alive(pid: int) -> bool:
    """Whether process ``pid`` runs (no psutil: the pyautogui venv has none)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stale_reason(info: dict | None, now: float) -> str:
    """Why the lock (as read: ``None`` when unreadable) may be taken over; ``""`` when it
    is held."""
    if info is None:
        try:
            age = now - LOCK.stat().st_mtime
        except OSError:
            return ""
        return f"unreadable and unchanged for {age:.0f} s" if age > STALE_S else ""
    pid = info.get("pid")
    if isinstance(pid, int) and not pid_alive(pid):
        return f"its holder (pid {pid}) is gone"
    beat = info.get("beat")
    if not isinstance(beat, (int, float)):
        return "it records no beat"
    return f"no beat for {now - beat:.0f} s" if now - beat > STALE_S else ""


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
            text = _read_text()
            info = _parse(text)
            if info is not None and info.get("name") == name:
                _write({**info, "beat": now})
                print(f"[OK] desktop lock already held by {name}")
                return True
            why = stale_reason(info, now)
            # take it over only as judged: a lock rewritten meanwhile is someone's new one
            if why and _read_text() == text:
                holder = (info or {}).get("name", "?")
                print(f"[WARN] taking over the stale desktop lock of {holder}: {why}")
                LOCK.unlink(missing_ok=True)
                continue
            if not announced:
                print(f"[..] desktop busy ({(info or {}).get('name', 'being written')}); waiting")
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
    run's; otherwise take it as ``name`` plus a nonce (a recycled pid never matches an
    old lock), waiting up to ``wait_s``, and free it at the end. ``SystemExit`` when
    someone else holds it past the wait. A private display (ENV_PRIVATE) needs none."""
    if os.environ.get(ENV_PRIVATE):
        yield
        return
    outer = os.environ.get(ENV_NAME, "")
    if outer and (_read() or {}).get("name") == outer:
        yield
        return
    name = f"{name}-{secrets.token_hex(3)}"
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
