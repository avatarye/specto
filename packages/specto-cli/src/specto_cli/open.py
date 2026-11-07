"""``specto open [PATH]`` (#215): start the desktop app on a project from any terminal,
detached, and say which instance it became. Qt-free; the app is another process.

- The app home is the Specto checkout whose venv runs ``specto-app``, found in order:
  ``SPECTO_APP_HOME``, ``<user dir>/cli.json`` (``specto setup-cli`` writes it), the
  checkout this CLI is installed from (``uv tool install --editable`` keeps the source
  there). ``resolve_app_home`` says which and why, or fails with the command to run.
- ``launch`` starts ``<home>/.venv python -m specto_app --project <root>`` with its
  output appended to a log, in its own process group and with no console window
  (Windows: ``CREATE_NO_WINDOW``, a hidden console the app's own console children
  inherit, so neither closing the terminal nor the agent subprocess pops a window;
  elsewhere ``start_new_session``), and drops the handle: the CLI returns at once. The
  helper is ``specto_core.detached.spawn_detached``, shared with the app's project
  switch (SPEC-28).
- ``wait_for_instance`` watches the instance registry (``specto_core.instances``) for
  the new process's entry, so the CLI can print the instance name.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from specto_core.errors import SpectoError
from specto_core.instances import Instance, list_instances, pid_alive, project_key, user_dir

ENV_APP_HOME = "SPECTO_APP_HOME"
CLI_SETTINGS = "cli.json"
LAUNCH_LOG = "launch.log"
SETUP_FIX = (
    "run `specto setup-cli <specto checkout>` once (the checkout holds packages/specto-app "
    f"and a synced .venv), or set {ENV_APP_HOME}=<checkout>"
)


# -- the app home ----------------------------------------------------------------------------
@dataclass(frozen=True)
class AppHome:
    root: Path
    source: str  # "env", "settings" or "checkout"

    @property
    def python(self) -> Path:
        return venv_python(self.root)


def venv_python(home: Path | str) -> Path:
    """The venv interpreter under ``home`` (``.venv/Scripts/python.exe`` on Windows,
    ``.venv/bin/python`` elsewhere)."""
    home = Path(home)
    if sys.platform == "win32":
        return home / ".venv" / "Scripts" / "python.exe"
    return home / ".venv" / "bin" / "python"


def home_problem(home: Path | str) -> str | None:
    """Why ``home`` cannot launch the app, or None when it can."""
    home = Path(home).expanduser()
    if not home.is_dir():
        return f"{home} is not a folder"
    if not (home / "packages" / "specto-app").is_dir():
        return f"{home} is not a Specto checkout (no packages/specto-app)"
    py = venv_python(home)
    if not py.is_file():
        return f"{home} has no synced venv ({py.relative_to(home)} missing); run `uv sync` there"
    return None


def settings_path() -> Path:
    return user_dir() / CLI_SETTINGS


def load_settings() -> dict[str, Any]:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_app_home(home: Path | str) -> Path:
    """``specto setup-cli``: keep ``home`` in ``<user dir>/cli.json``."""
    data = load_settings()
    data["app_home"] = str(Path(home).expanduser().resolve())
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def checkout_of(module_file: str | Path | None = None) -> Path | None:
    """The Specto checkout this CLI's source lives in (an editable install), if any."""
    here = Path(module_file or __file__).resolve()
    for parent in here.parents:
        if (parent / "packages" / "specto-app").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    return None


def candidates(environ: dict[str, str] | None = None) -> list[tuple[str, Path]]:
    """Where the app home may come from, in precedence order: (source, path)."""
    env = os.environ if environ is None else environ
    out: list[tuple[str, Path]] = []
    if env.get(ENV_APP_HOME, "").strip():
        out.append(("env", Path(env[ENV_APP_HOME].strip()).expanduser()))
    kept = str(load_settings().get("app_home") or "").strip()
    if kept:
        out.append(("settings", Path(kept).expanduser()))
    checkout = checkout_of()
    if checkout is not None:
        out.append(("checkout", checkout))
    return out


def resolve_app_home(environ: dict[str, str] | None = None) -> AppHome:
    """The first usable candidate. A candidate that is set but broken is an error (a
    stale setting should be fixed, not silently skipped)."""
    found = candidates(environ)
    if not found:
        raise SpectoError("cannot find the Specto app to launch", fix=SETUP_FIX)
    source, root = found[0]
    problem = home_problem(root)
    if problem is None:
        return AppHome(root=root.resolve(), source=source)
    where = {
        "env": f"{ENV_APP_HOME}",
        "settings": f"{settings_path()} (from `specto setup-cli`)",
        "checkout": "this install's checkout",
    }[source]
    raise SpectoError(f"the Specto app home from {where} is unusable: {problem}", fix=SETUP_FIX)


# -- launching -------------------------------------------------------------------------------
def default_log_path() -> Path:
    return user_dir() / LAUNCH_LOG


def launch_argv(
    home: AppHome, project_root: Path | None, app_args: list[str] | None = None
) -> list[str]:
    """``python -m specto_app [--project ROOT] [app args]``; no ``--project`` opens the
    welcome screen."""
    argv = [str(home.python), "-m", "specto_app"]
    if project_root is not None:
        argv += ["--project", str(project_root)]
    return argv + list(app_args or [])


def child_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The app's environment: ours, minus the project/endpoint overrides a CLI caller may
    carry (the app serves the project it is given, not the shell's)."""
    env = dict(os.environ if environ is None else environ)
    env.pop("SPECTO_PROJECT", None)
    env.pop("SPECTO_ENDPOINT", None)
    return env


def _child_alive(pid: int) -> bool:
    """Whether the app this process launched still runs. On Linux and macOS an exited child
    stays a zombie until reaped (``spawn_detached`` keeps no handle to wait on), and a zombie
    answers ``kill(pid, 0)``: reap it first. Not our child (Windows, a venv launcher's
    interpreter): :func:`pid_alive`."""
    if sys.platform != "win32":
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        else:
            if done == pid:
                return False
    return pid_alive(pid)


def instances_of(project_root: Path | str) -> list[Instance]:
    """The live instances open on ``project_root``."""
    key = project_key(project_root)
    return [i for i in list_instances() if project_key(i.project) == key]


def wait_for_instance(
    project_root: Path | str,
    pid: int,
    timeout: float,
    *,
    before: list[Instance] | None = None,
    poll: float = 0.2,
) -> Instance | None:
    """The registry entry the app started as process ``pid`` writes for ``project_root``,
    within ``timeout`` seconds; None when none appeared in time or ``pid`` ended first.
    Matched by project, not pid: a venv's ``python.exe`` on Windows is a launcher that
    runs the interpreter as a child, so the registered pid is the child's. ``before``
    (the project's instances when the launch started, ``--new``) are not it."""
    key = project_key(project_root)
    seen = {(i.name, i.pid) for i in before or ()}
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        for inst in list_instances(sweep=False):
            if project_key(inst.project) == key and (inst.name, inst.pid) not in seen:
                return inst
        if not _child_alive(pid) or time.monotonic() >= deadline:
            return None
        time.sleep(poll)


def log_tail(log_path: Path, lines: int = 12) -> str:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.rstrip().splitlines()[-lines:])


def allow_foreground(pid: int) -> None:
    """Windows lets the foreground process hand its right to take the foreground to
    another process: without it the app's ``requestActivate`` only flashes its taskbar
    entry. No-op elsewhere, and harmless when refused."""
    if sys.platform != "win32":
        return
    import ctypes

    with contextlib.suppress(Exception):
        ctypes.WinDLL("user32", use_last_error=True).AllowSetForegroundWindow(int(pid))


__all__ = [
    "CLI_SETTINGS",
    "ENV_APP_HOME",
    "AppHome",
    "allow_foreground",
    "candidates",
    "checkout_of",
    "child_env",
    "default_log_path",
    "home_problem",
    "instances_of",
    "launch_argv",
    "load_settings",
    "log_tail",
    "resolve_app_home",
    "save_app_home",
    "settings_path",
    "venv_python",
    "wait_for_instance",
]
