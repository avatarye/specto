"""``specto-app [--project PATH]``: the desktop app entry point.

One project, one canvas, one OS window (ADR 0012). The project is ``--project`` (created
if the folder has no ``.specto/`` yet) or ``SPECTO_PROJECT``; with neither, the welcome
screen offers recent projects and "Open folder..." (a project opens, a plain folder
becomes one, #217). While the app runs,
``specto`` (CLI), the SDK and ``specto-mcp`` reach it through ``.specto/endpoint.json``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from specto_core import Project
from specto_core.errors import SpectoError


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="specto-app", description=__doc__.split("\n\n")[1])
    ap.add_argument("--project", "-p", type=Path, default=None, help="project folder")
    ap.add_argument(
        "--host-module",
        action="append",
        default=[],
        metavar="MODULE",
        help="import MODULE to register extra view hosts (repeatable)",
    )
    ap.add_argument("--inline-jobs", action="store_true", help="no worker pool (debugging)")
    ap.add_argument("--workers", type=int, default=None, help="worker count (default cores-2)")
    ap.add_argument("--no-web", action="store_true", help="do not initialise QtWebEngine")
    ap.add_argument("--no-serve", action="store_true", help="do not open the command endpoint")
    ap.add_argument("--offscreen", action="store_true", help="offscreen platform (tests)")
    ap.add_argument(
        "--agent",
        default="claude",
        metavar="SPEC",
        help="the window's agent (the S icon): claude (Claude Code over the Agent SDK, default) or "
        "scripted:<script.json> (a scripted agent, GUI flows)",
    )
    ap.add_argument(
        "--test-driver",
        action="store_true",
        help="open the GUI test driver (tools/gui_qtdrive.py drives the flows through Qt's "
        "test API; a fake clipboard, muted audio, fake launchers, no window activation)",
    )
    return ap


def resolve_project(path: Path | None) -> Project:
    explicit = path or (
        Path(os.environ["SPECTO_PROJECT"]) if os.environ.get("SPECTO_PROJECT") else None
    )
    if explicit is not None:
        explicit = explicit.expanduser()
        created = not (explicit / ".specto").is_dir()
        proj = Project.open_or_init(explicit)
        if created:
            print(f"[OK] initialised project {proj.name!r} at {proj.root}")
        return proj
    proj = Project.find()
    if proj is None:
        raise SpectoError(
            "no Specto project here (no .specto/ at or above the current directory)",
            fix="pass --project <folder> (it is created if needed) or cd into a project",
        )
    return proj


def passthrough_flags(args: argparse.Namespace) -> list[str]:
    """The flags a relaunch on another project keeps (not the project/canvas ones)."""
    out: list[str] = []
    for mod in args.host_module:
        out += ["--host-module", mod]
    if args.inline_jobs:
        out.append("--inline-jobs")
    if args.workers is not None:
        out += ["--workers", str(args.workers)]
    if args.no_web:
        out.append("--no-web")
    if args.no_serve:
        out.append("--no-serve")
    if args.offscreen:
        out.append("--offscreen")
    if args.agent != "claude":
        out += ["--agent", args.agent]
    if args.test_driver:
        out.append("--test-driver")
    return out


def app_options(args: argparse.Namespace) -> dict:
    return {
        "jobs": "inline" if args.inline_jobs else "pool",
        "workers": args.workers,
        "web": False if args.no_web else None,
        "host_modules": tuple(args.host_module),
        "offscreen": args.offscreen,
        "serve": not args.no_serve,
        "launch_flags": passthrough_flags(args),
        "agent": args.agent,
        "test_driver": args.test_driver,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    opts = app_options(args)
    if args.project is None and not os.environ.get("SPECTO_PROJECT"):
        # no project named: the welcome screen (recents, open folder, new project)
        from .launcher import run_welcome

        return run_welcome(opts)
    try:
        project = resolve_project(args.project)
    except SpectoError as e:
        print(f"[ERROR] {e.message}" + (f"  -> {e.fix}" if e.fix else ""), file=sys.stderr)
        return 2
    from .app import SpectoApp

    try:
        app = SpectoApp(project, **opts)
        app.start()
    except SpectoError as e:
        print(f"[ERROR] {e.message}" + (f"  -> {e.fix}" if e.fix else ""), file=sys.stderr)
        return 1
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
