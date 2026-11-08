"""The launchers of one project (#186): ``LauncherBook`` reads both files again whenever
one changed (a stat per use, and the shell's watcher), answers which launchers open a
selection, and launches: a configured launcher, the system default (refused for
programs and scripts) or Explorer's reveal. Every failure is a ``SpectoError`` with a
fix-it; every launch is logged. No Qt here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from specto_core.errors import InvalidArgs, NotFound, SpectoError
from specto_core.pathguard import path_refusal

from ..recents import user_data_dir
from . import run
from .config import (
    MACHINE_FILE,
    PROJECT_FILE,
    PROJECT_TEMPLATE,
    TEMPLATE,
    Launcher,
    Problem,
    batch_problem,
    load,
    refused_reason,
)

#: the system default's id where a launcher id goes (the agent's ``launch``)
SYSTEM = "system"
SYSTEM_LABEL = "System default"

Item = tuple[str, bool]  # an absolute path, and whether it is a folder


def file_of(path: str) -> str:
    """The file behind a target item's path: a container entry (``file.zip#inner``, the
    gallery's) is its container. "" for a path whose text ``specto_core.pathguard``
    refuses (a network or device path is never looked for, SPEC-76)."""
    if path and path_refusal(path):
        return ""
    if not path or os.path.exists(path):
        return path
    head, sep, _ = path.partition("#")
    return head if sep and os.path.exists(head) else path


@dataclass
class Launched:
    """One launch: what ran, on what, as which processes; or, with ``refused``, why it
    did not (#192: an agent's refused launch is a thread row too)."""

    launcher: str
    label: str
    paths: list[str]
    argvs: list[list[str]] = field(default_factory=list)
    pids: list[int] = field(default_factory=list)
    refused: str = ""

    @property
    def note(self) -> str:
        """``launched Blender 4.2 LTS on scene.blend``, or ``refused System default on
        setup.exe: <why>`` (the agent thread's row)."""
        names = [os.path.basename(os.path.normpath(str(p))) or str(p) for p in self.paths]
        if not names:
            on = "no files"
        else:
            on = names[0] if len(names) == 1 else f"{names[0]} and {len(names) - 1} more"
        if self.refused:
            return f"refused {self.label} on {on}: {self.refused}"
        return f"launched {self.label} on {on}"


class LauncherBook:
    """The machine's and the project's launchers, merged (``config``)."""

    def __init__(
        self,
        root: Path | str | None,
        *,
        machine_path: Path | None = None,
        spawn: Callable[..., int] | None = None,
        start: Callable[[str], None] | None = None,
        reveal: Callable[[Sequence[str]], None] | None = None,
        edit: Callable[[str], None] | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        """``spawn`` / ``start`` / ``reveal`` / ``edit`` default to ``run``'s functions,
        looked up at each call (the tests' guard replaces them there)."""
        self.root = Path(root) if root else None
        self.machine_path = Path(machine_path or user_data_dir() / MACHINE_FILE)
        self.project_path = self.root / PROJECT_FILE if self.root is not None else None
        self.spawn = spawn or (lambda argv, cwd="", env=None: run.spawn_program(argv, cwd, env))
        self.start = start or (lambda path: run.start_file(path))
        self.reveal_fn = reveal or (lambda paths: run.reveal(paths))
        self.edit_fn = edit or (lambda path: run.edit_file(path))
        self.log = log
        self.launchers: list[Launcher] = []
        self.problems: list[Problem] = []
        #: called with the book after a reload that found the files changed
        self.listeners: list[Callable[[LauncherBook], None]] = []
        self._key: tuple[Any, ...] | None = None
        self.refresh()

    # -- reading ---------------------------------------------------------------------------------
    @property
    def files(self) -> list[Path]:
        return [p for p in (self.machine_path, self.project_path) if p is not None]

    def _stat_key(self) -> tuple[Any, ...]:
        out: list[Any] = []
        for p in self.files:
            try:
                st = p.stat()
                out.append((st.st_mtime_ns, st.st_size))
            except OSError:
                out.append(None)
        return tuple(out)

    def refresh(self, *, force: bool = False) -> bool:
        """Read the files again when they changed (or ``force``); True when it did. The
        listeners hear of every reload but the first."""
        key = self._stat_key()
        if key == self._key and not force:
            return False
        first = self._key is None
        self._key = key
        self.launchers, self.problems = load(self.machine_path, self.project_path)
        if not first:
            for fn in list(self.listeners):
                fn(self)
        return True

    def all(self) -> list[Launcher]:
        self.refresh()
        return list(self.launchers)

    def get(self, launcher_id: str) -> Launcher | None:
        return next((lc for lc in self.all() if lc.id == launcher_id), None)

    def matching(self, items: Sequence[Item]) -> list[Launcher]:
        """The launchers that open every item."""
        return [lc for lc in self.all() if lc.matches_all(items)]

    def default_for(self, items: Sequence[Item]) -> Launcher | None:
        """The preferred launcher for the items: a default the project's file chose over
        the machine's, then the first in file order."""
        found = [lc for lc in self.matching(items) if lc.default]
        return max(found, key=lambda lc: lc.default_rank) if found else None

    @staticmethod
    def system_refusal(paths: Sequence[str]) -> str | None:
        """Why the system default may not open these (one of them would run), or None."""
        return next((r for r in (refused_reason(p) for p in paths) if r), None)

    # -- paths ----------------------------------------------------------------------------------
    def resolve(self, paths: Sequence[Any]) -> list[str]:
        """Absolute native paths for ``paths`` (relative ones are the project's); every
        one must exist. A network or device path is refused by its text before any
        filesystem call (``specto_core.pathguard``, SPEC-76)."""
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            raise InvalidArgs("no files to open", fix="paths: the files, relative to the project")
        out: list[str] = []
        for p in paths:
            text = str(p or "").strip()
            why = path_refusal(text)
            if why:
                raise InvalidArgs(
                    f"refused: {text} is {why}",
                    fix="a path on this machine, relative to the project folder",
                )
            path = Path(text)
            if not path.is_absolute() and self.root is not None:
                path = self.root / path
            full = file_of(os.path.normpath(str(path)))
            if not text or not os.path.exists(full):
                raise NotFound(
                    f"no such file: {text or '(empty)'}",
                    fix="a path relative to the project folder (list_files finds it)",
                )
            out.append(full)
        return out

    # -- launching ------------------------------------------------------------------------------
    def command_path(self, launcher: Launcher) -> str:
        """The launcher's program: its path, or the program found on PATH. A command
        whose text (variables expanded) is a network or device path is refused before it
        is looked for, and so never runs (``specto_core.pathguard``, SPEC-76)."""
        cmd = os.path.expandvars(os.path.expanduser(launcher.command))
        where = f"{launcher.file}:{launcher.line}" if launcher.line else launcher.file
        why = path_refusal(cmd)
        if why:
            raise InvalidArgs(
                f"the launcher {launcher.label!r} names {cmd}, {why}: not run",
                fix=f"give a program on this machine as its command in {where} (Edit launchers...)",
            )
        if os.path.isabs(cmd) or os.sep in cmd or "/" in cmd:
            if os.path.isfile(cmd):
                return os.path.normpath(cmd)
            raise NotFound(
                f"the launcher {launcher.label!r} names a program that is not there: {cmd}",
                fix=f"correct its command in {where} (Edit launchers...)",
            )
        found = shutil.which(cmd)
        if found is None:
            raise NotFound(
                f"the launcher {launcher.label!r} names {cmd!r}, which is not on PATH",
                fix=f"give its full path as command in {where} (Edit launchers...)",
            )
        return found

    def launch(self, launcher_id: str, paths: Sequence[Any]) -> Launched:
        """Open ``paths`` with the launcher ``launcher_id`` (``system``: the system
        default)."""
        if launcher_id == SYSTEM:
            return self.open_default(paths)
        launcher = self.get(launcher_id)
        if launcher is None:
            have = ", ".join(lc.id for lc in self.launchers) or "none configured"
            raise NotFound(
                f"no launcher {launcher_id!r}",
                fix=f"launchers: {have}; or {SYSTEM!r} for the system default",
            )
        return self.run(launcher, self.resolve(paths))

    def run(self, launcher: Launcher, paths: list[str]) -> Launched:
        items = [(p, os.path.isdir(p)) for p in paths]
        if not launcher.matches_all(items):
            odd = next(p for p, d in items if not launcher.matches(p, d))
            others = ", ".join(lc.id for lc in self.matching(items)) or "none"
            raise InvalidArgs(
                f"{launcher.label} does not open {os.path.basename(odd)} "
                f"(it matches {', '.join(launcher.match)})",
                fix=f"launchers for these files: {others}",
            )
        command = self.command_path(launcher)
        root = str(self.root) if self.root is not None else ""
        invocations = launcher.invocations(paths, root)
        for inv in invocations:  # every check before the first process starts
            why = batch_problem(command, inv.argv[1:])
            if why:
                raise InvalidArgs(
                    why, fix="point the launcher at the program itself, not a .bat or .cmd"
                )
            why = path_refusal(inv.cwd) if inv.cwd else None
            if why:
                raise InvalidArgs(
                    f"the launcher {launcher.label!r} starts in {inv.cwd}, {why}",
                    fix=f"correct its cwd in {launcher.file}:{launcher.line}",
                )
            if inv.cwd and not os.path.isdir(inv.cwd):
                raise NotFound(
                    f"the launcher {launcher.label!r} starts in {inv.cwd}, which is not a folder",
                    fix=f"correct its cwd in {launcher.file}:{launcher.line}",
                )
        done = Launched(launcher.id, launcher.label, list(paths))
        for inv in invocations:
            argv = [command, *inv.argv[1:]]
            try:
                pid = self.spawn(argv, inv.cwd, dict(inv.env) or None)
            except OSError as e:
                raise SpectoError(
                    f"{launcher.label} did not start: {e}",
                    fix=f"check its command in {launcher.file}:{launcher.line}",
                ) from e
            done.argvs.append(argv)
            done.pids.append(pid)
            self.log(f"[OK] launched {launcher.label}: {subprocess.list2cmdline(argv)} (pid {pid})")
        return done

    def open_default(self, paths: Sequence[Any]) -> Launched:
        """Each file with the system's default program; refused when one is a program or
        a script (it would run)."""
        full = self.resolve(paths)
        why = self.system_refusal(full)
        if why:
            raise InvalidArgs(
                f"refused: {why}",
                fix="configure a launcher for it (Edit launchers...), or Show in Explorer",
            )
        for p in full:
            try:
                self.start(p)
            except OSError as e:
                raise SpectoError(
                    f"the system has no program for {os.path.basename(p)}: {e}",
                    fix="configure a launcher for it (Edit launchers...)",
                ) from e
            self.log(f"[OK] launched the system default on {p}")
        return Launched(SYSTEM, SYSTEM_LABEL, full)

    def show_in_explorer(self, paths: Sequence[Any]) -> Launched:
        full = self.resolve(paths)
        try:
            self.reveal_fn(full)
        except OSError as e:
            raise SpectoError(f"Explorer did not start: {e}") from e
        self.log(f"[OK] revealed {full[0]}")
        return Launched("explorer", "Explorer", full)

    def edit(self) -> str:
        """Open the machine's launcher file in the system's editor, writing a commented
        example first when there is none; its path."""
        return self._edit(self.machine_path, TEMPLATE)

    def edit_project(self) -> str:
        """Open the project's launcher file (``<project>/.specto/launchers.toml``, #204)
        in the system's editor, writing a commented example first when there is none;
        its path. ``InvalidArgs`` for the machine's book (no project)."""
        if self.project_path is None:
            raise InvalidArgs("no project: only the machine's launchers", fix="open a project")
        return self._edit(self.project_path, PROJECT_TEMPLATE)

    def _edit(self, path: Path, template: str) -> str:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(template.encode("utf-8"))
        try:
            self.edit_fn(str(path))
        except OSError as e:
            raise SpectoError(
                f"no editor opened {path}: {e}", fix=f"open {path} in a text editor"
            ) from e
        self.log(f"[OK] editing {path}")
        return str(path)

    # -- the agent's view -------------------------------------------------------------------------
    def summary(self, paths: Sequence[Any] | None = None) -> dict[str, Any]:
        """What the agent's ``launchers`` tool returns: every launcher, or with ``paths``
        those opening all of them, the default one and whether the system default may."""
        out: dict[str, Any] = {}
        if paths:
            full = self.resolve(paths)
            items = [(p, os.path.isdir(p)) for p in full]
            found = self.matching(items)
            default = self.default_for(items)
            out["launchers"] = [lc.describe() for lc in found]
            out["default"] = default.id if default is not None else None
            why = self.system_refusal(full)
            out["system_default"] = {"allowed": why is None, **({"refused": why} if why else {})}
        else:
            out["launchers"] = [lc.describe() for lc in self.all()]
            out["system_default"] = {"allowed": True, "note": "refused for programs and scripts"}
        out["files"] = {
            "machine": str(self.machine_path),
            "project": str(self.project_path) if self.project_path else None,
        }
        if self.problems:
            out["problems"] = [p.text() for p in self.problems]
        return out


_BOOKS: dict[tuple[str, str], LauncherBook] = {}


def book_for(root: Path | str | None) -> LauncherBook:
    """The book of a project folder (None: the machine's alone), one per folder and
    machine file; the menu's entries and the app share it."""
    key = (os.path.normcase(os.path.normpath(str(root))) if root else "", str(user_data_dir()))
    book = _BOOKS.get(key)
    if book is None:
        book = _BOOKS[key] = LauncherBook(root)
    return book


__all__ = ["SYSTEM", "Launched", "LauncherBook", "book_for", "file_of"]
