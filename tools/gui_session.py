"""Launch specto-app on screen, run one or more gui_drive flows against it, kill it.

    uv run python tools/desktop_lock.py run <name> -- <pyauto python> tools/gui_session.py \\
        --project <folder> --out out/verify/m2 [--fresh] [--app-log <file>] flow1.txt [flow2.txt ...]

Runs with the pyautogui venv's interpreter (see gui_drive.py); the app itself starts
through ``uv run specto-app --project <folder> --inline-jobs`` from this repo. Only the
app process this script started is killed. ``--fresh`` wipes the project folder first.
The app keeps its settings (model choice, recents, trust list) in
``<project>/.specto/gui-user`` unless ``SPECTO_USER_DIR`` is already set (#89).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from console_utf8 import utf8_stdio  # noqa: E402
from gui_platform import kill_tree, popen_kwargs  # noqa: E402


def app_env(project: Path, environ: dict[str, str]) -> dict[str, str]:
    """The app's environment: a scratch user settings dir inside the project (kept across
    sessions on it, so a relaunch still finds the recent list; ``--fresh`` wipes it) unless
    the caller set one, and unbuffered output so the app's [OK]/[WARN] lines reach the log
    before it is killed."""
    env = {**environ, "PYTHONUNBUFFERED": "1"}
    env.setdefault("SPECTO_USER_DIR", str(project / ".specto" / "gui-user"))
    return env


def welcome(
    app: subprocess.Popen,
    project: Path,
    out: Path,
    *,
    prefix: str = "final",
    recent: bool = False,
    only: bool = False,
) -> None:
    """The welcome card: screenshot it, then open the project by typing its folder and
    pressing Enter, or (``recent``) by clicking the first row of the recent list. With
    ``only`` the card is screenshotted again after the attempt (a refusal stays on the
    card) and the app is left on the welcome screen."""
    import pyautogui

    from gui_platform import activate, client_origin, find_window, screenshot, shot_region

    hwnd = None
    for _ in range(600):
        if app.poll() is not None:
            raise SystemExit(f"[ERROR] app exited early with {app.returncode}")
        try:
            hwnd = find_window("Specto$")
            break
        except SystemExit:
            time.sleep(0.1)
    if hwnd is None:
        raise SystemExit("[ERROR] no welcome window")
    time.sleep(1.5)
    activate(hwnd)
    time.sleep(0.3)
    x, y, w, h = client_origin(hwnd)
    region = shot_region(hwnd)
    out.mkdir(parents=True, exist_ok=True)
    screenshot(region).save(out / f"{prefix}-01-welcome.png")
    if recent:
        # the first row of "Recent projects" in the card's right column (Welcome.qml)
        pyautogui.click(x + int(w * 0.70), y + int(h * 0.245))
    else:
        # the "or type a folder" field sits in the card's left column (Welcome.qml; one
        # action button above it since #217, so it moved up 42 px of the 560 px window)
        pyautogui.click(x + int(w * 0.24), y + int(h * 0.46))
        pyautogui.typewrite(str(project), interval=0.01)
        time.sleep(0.3)
        screenshot(region).save(out / f"{prefix}-01b-welcome-typed.png")
        pyautogui.press("enter")
    if only:
        time.sleep(1.5)
        screenshot(region).save(out / f"{prefix}-02-welcome-after.png")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--app-log", type=Path, default=None)
    ap.add_argument("--keep", action="store_true", help="leave the app running")
    ap.add_argument("--pool", action="store_true", help="worker pool instead of --inline-jobs")
    ap.add_argument("--extra", default="", help="extra specto-app flags")
    ap.add_argument(
        "--welcome",
        action="store_true",
        help="start with no project: type the project path on the welcome card (M1.5 step 1)",
    )
    ap.add_argument("--shot-prefix", default="final", help="welcome screenshot prefix")
    ap.add_argument(
        "--welcome-recent",
        action="store_true",
        help="with --welcome: open the first recent project instead of typing the path",
    )
    ap.add_argument(
        "--welcome-only",
        action="store_true",
        help="with --welcome: screenshot the card after the attempt and stop (a refusal)",
    )
    ap.add_argument("flows", nargs="*", type=Path)
    ns = ap.parse_args()
    # the flow log is a file: a chip's glyph (the peers chip's U+25BE) must not kill the
    # run with UnicodeEncodeError under a cp1252 default (#147's fix, here too)
    utf8_stdio()
    if ns.fresh and ns.project.exists():
        shutil.rmtree(ns.project)
    ns.project.mkdir(parents=True, exist_ok=True)
    ep = ns.project / ".specto" / "endpoint.json"
    if ep.exists():
        ep.unlink()
    log = open(ns.app_log, "w", encoding="utf-8") if ns.app_log else None
    cmd = ["uv", "run", "specto-app", *ns.extra.split()]
    if not ns.pool:
        cmd.append("--inline-jobs")
    if not ns.welcome:
        cmd += ["--project", str(ns.project)]
    env = app_env(ns.project, dict(os.environ))
    app = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=log or subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        env=env,
        **popen_kwargs(),
    )
    print(f"[OK] app pid {app.pid}")
    try:
        if ns.welcome:
            welcome(
                app,
                ns.project,
                ns.out,
                prefix=ns.shot_prefix,
                recent=ns.welcome_recent,
                only=ns.welcome_only,
            )
            if ns.welcome_only:
                print("[OK] welcome done")
                return 0
        for _ in range(300):
            if ep.exists() and app.poll() is None:
                break
            if app.poll() is not None:
                print(f"[ERROR] app exited early with {app.returncode}")
                return 1
            time.sleep(0.1)
        else:
            print("[ERROR] endpoint never appeared")
            return 1
        time.sleep(1.5)
        from gui_drive import Driver

        d = Driver(ns.project, ns.out, f"Specto - {ns.project.name}")
        for flow in ns.flows:
            print(f"[..] flow {flow}")
            for line in flow.read_text(encoding="utf-8").splitlines():
                d.run(line.strip())
        print("[OK] flows done")
        return 0
    except SystemExit as e:
        print(f"[ERROR] {e}")
        return 2
    finally:
        if not ns.keep and app.poll() is None:
            # uv run wraps the app: end the whole tree, or the app outlives this script
            kill_tree(app)
            try:
                app.wait(5)
            except subprocess.TimeoutExpired:
                app.kill()
        if log:
            log.close()


if __name__ == "__main__":
    sys.exit(main())
