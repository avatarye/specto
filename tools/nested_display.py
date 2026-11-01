"""Run a GUI command on a nested X server of its own (Linux): the real-mouse flows
(gui_session.py, gui_drive.py, v2_suite.py) move the pointer and type, which must not
happen on the desktop someone is using. Starts ``Xephyr`` on the first free display,
runs the command with ``DISPLAY`` pointing at it, and stops the server afterwards.

    uv run python tools/nested_display.py run [--size 1920x1080] -- <command ...>

Xephyr opens one window on the current desktop (``DISPLAY``) and keeps its own pointer,
keyboard and clipboard inside it; no window manager runs there, so app windows sit at
the positions Qt asks for and take the focus they request. GL inside it is Mesa's
(llvmpipe), not the GPU's: Qt WebEngine has no WebGL there (Chromium blocklists it,
with the blocklist ignored its GPU compositing draws every web view black, and this
build has no SwiftShader WebGL), so flows that need WebGL (the 3D views, a map's layers,
a 3D preview) run on the GPU's display under ``desktop_lock.py`` instead. The 2D web
views (json, markdown, html, pdf) paint. ``sudo apt install xserver-xephyr`` if it is
missing.

Each run has a display of its own, so several can run at once without
``desktop_lock.py``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def free_display(start: int = 20) -> int:
    """The first display number with neither a lock file nor a socket."""
    for n in range(start, start + 100):
        if not Path(f"/tmp/.X{n}-lock").exists() and not Path(f"/tmp/.X11-unix/X{n}").exists():
            return n
    raise SystemExit("[ERROR] no free X display number")


def wait_ready(display: str, server: subprocess.Popen, timeout: float = 15.0) -> None:
    """Poll until a client can connect to the new server."""
    deadline = time.monotonic() + timeout
    env = {**os.environ, "DISPLAY": display}
    probe = shutil.which("xdpyinfo")
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise SystemExit(f"[ERROR] Xephyr exited with {server.returncode}")
        if probe:
            ok = subprocess.call([probe], env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL) == 0
        else:
            ok = Path(f"/tmp/.X11-unix/X{display.lstrip(':')}").exists()
        if ok:
            return
        time.sleep(0.2)
    raise SystemExit(f"[ERROR] display {display} not ready after {timeout:.0f} s")


def run(cmd: list[str], size: str) -> int:
    xephyr = shutil.which("Xephyr")
    if not xephyr:
        raise SystemExit("[ERROR] Xephyr not found: sudo apt install xserver-xephyr")
    n = free_display()
    display = f":{n}"
    # -no-host-grab: a click into its window never captures the host's keyboard and mouse
    args = [xephyr, display, "-screen", size, "-ac", "-br", "-noreset", "-nolisten", "tcp"]
    args.append("-no-host-grab")
    server = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_ready(display, server)
        print(f"[OK] nested display {display} ({size})", flush=True)
        return subprocess.call(cmd, env={**os.environ, "DISPLAY": display})
    finally:
        server.terminate()
        try:
            server.wait(5)
        except subprocess.TimeoutExpired:
            server.kill()
        print(f"[OK] nested display {display} stopped", flush=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("op", choices=["run"])
    ap.add_argument("--size", default="1920x1080", help="the nested screen, WxH")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    ns = ap.parse_args(argv)
    if sys.platform == "win32":
        raise SystemExit("[ERROR] nested_display.py is for X11; on Windows use desktop_lock.py")
    cmd = ns.cmd[1:] if ns.cmd[:1] == ["--"] else ns.cmd
    if not cmd:
        ap.error("run needs a command after --")
    return run(cmd, ns.size)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
