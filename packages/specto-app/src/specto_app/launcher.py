"""Project open/new without a CLI (HLD 5.6, principle 5).

- ``run_welcome(opts)``: ``specto-app`` with no ``--project`` shows ``Welcome.qml`` (style
  B card: recent projects and one "Open folder...", which opens the folder's project or
  makes one there, #217). Picking one starts ``SpectoApp`` on it in this same process
  (nothing was open yet).
- ``relaunch(root, flags)``: from a running app, switching projects spawns a new
  ``specto-app --project <root>`` process (one project per process, HLD 5.6) and the
  caller quits. Output of the child goes to ``<user data dir>/launch.log``.

``WelcomeController`` is Qt but window-free, so it is testable offscreen.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from specto_core import Project
from specto_core.detached import console_python, spawn_detached
from specto_core.errors import SpectoError

from . import recents
from .branding import APP_ARGV0, install_app_icon
from .hosts.base import SHELL_QML

WELCOME_QML = SHELL_QML / "Welcome.qml"


def resolve_folder(text: str) -> Path | None:
    """A folder from user text or a ``file://`` url; None when empty."""
    s = (text or "").strip().strip('"')
    if not s:
        return None
    if s.startswith("file:"):
        s = QUrl(s).toLocalFile()
    return Path(s).expanduser()


def open_project(folder: Path, *, create: bool) -> Project:
    """Open a project folder; with ``create`` an ordinary folder becomes one, named after
    itself (the welcome card's "Open folder..." and its path field, the app's menu, #217).
    Without it (a recent-list row, "Open the project of this folder") a folder that lost
    its ``.specto/`` is refused."""
    folder = Path(folder).expanduser()
    if recents.is_project(folder):
        proj = Project.open(folder)
    elif not create:
        raise SpectoError(
            f"{folder} is not a Specto project (no .specto/project.toml)",
            fix="pick it with 'Open folder...' (or type its path) to start a project there",
        )
    else:
        if folder.exists() and not folder.is_dir():
            raise SpectoError(f"{folder} is a file, not a folder")
        folder.mkdir(parents=True, exist_ok=True)
        proj = Project.init(folder)
        print(f"[OK] initialised project {proj.name!r} at {proj.root}")
    recents.remember(proj.root, proj.name)
    return proj


class WelcomeController(QObject):
    """What ``Welcome.qml`` binds to."""

    recentsChanged = Signal()
    errorChanged = Signal()
    projectChosen = Signal(str)  # project root

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._error = ""
        self._recents = recents.load_recents()
        here = Project.find()
        self._here = str(here.root) if here is not None else ""

    @Property("QVariantList", notify=recentsChanged)
    def recents(self) -> list[dict[str, Any]]:
        return [dict(e) for e in self._recents]

    @Property(str, constant=True)
    def hereProject(self) -> str:
        """The project the current folder is in, if any (a CLI user's expectation)."""
        return self._here

    @Property(str, notify=errorChanged)
    def error(self) -> str:
        return self._error

    def _set_error(self, text: str) -> None:
        if text != self._error:
            self._error = text
            self.errorChanged.emit()

    @Slot(str, bool, result=bool)
    def openPath(self, text: str, create: bool = False) -> bool:
        """Open (or with ``create`` initialise) the folder; emits ``projectChosen``."""
        folder = resolve_folder(text)
        if folder is None:
            self._set_error("type or choose a folder first")
            return False
        try:
            proj = open_project(folder, create=create)
        except SpectoError as e:
            self._set_error(e.message + (f"  -> {e.fix}" if e.fix else ""))
            return False
        except OSError as e:
            self._set_error(f"{folder}: {e}")
            return False
        self._set_error("")
        self._recents = recents.load_recents()
        self.recentsChanged.emit()
        self.projectChosen.emit(str(proj.root))
        return True

    @Slot(str)
    def forget(self, root: str) -> None:
        self._recents = recents.forget(root)
        self.recentsChanged.emit()

    @Slot(str, result=str)
    def shortPath(self, root: str) -> str:
        home = str(Path.home())
        if root.startswith(home):
            return "~" + root[len(home) :]
        return root


def start_app(root: Path | str, opts: dict[str, Any], loop: Any) -> Any:
    """Start ``SpectoApp`` on an opened project from the welcome screen, on the event
    loop the welcome already runs (the prompt bar's agent session lives on it)."""
    from .app import SpectoApp

    project = Project.open(Path(root))
    app = SpectoApp(project, **opts, loop=loop)
    app.start()
    return app


def run_welcome(opts: dict[str, Any], *, argv: list[str] | None = None) -> int:
    """Show the welcome window; on a choice start the app on that project in this
    process. Returns the process exit code. The loop is the app's qasync one from the
    start, so the prompt bar's agent runs once a project is open (#49)."""
    import asyncio

    import qasync
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    from . import testdrive
    from .qt_setup import install_qml, prepare

    prepare(web=opts.get("web"), offscreen=bool(opts.get("offscreen")))
    qapp = QGuiApplication.instance() or QGuiApplication(argv or [APP_ARGV0])
    install_app_icon(qapp)
    if opts.get("test_driver"):
        testdrive.enable()  # ``--test-driver`` (#237): drives the card, then the shell
    testdrive.start()
    engine = QQmlApplicationEngine()
    theme = install_qml(engine)
    welcome = WelcomeController()
    engine.rootContext().setContextProperty("welcome", welcome)
    engine.rootContext().setContextProperty(
        "shellWindowFlags", int(testdrive.window_flags(Qt.WindowType.Window))
    )
    engine.load(QUrl.fromLocalFile(str(WELCOME_QML)))
    if not engine.rootObjects():
        print(f"[ERROR] the welcome screen failed to load: {WELCOME_QML}", file=sys.stderr)
        return 1
    window = engine.rootObjects()[0]
    loop = qasync.QEventLoop(qapp)
    asyncio.set_event_loop(loop)
    state: dict[str, Any] = {"app": None, "theme": theme, "code": 0}

    def start(root: str) -> None:
        try:
            app = start_app(root, opts, loop)
        except SpectoError as e:
            print(f"[ERROR] {e.message}" + (f"  -> {e.fix}" if e.fix else ""), file=sys.stderr)
            state["code"] = 1
            qapp.quit()
            return
        state["app"] = app
        qapp.aboutToQuit.connect(app.close)
        window.close()
        window.deleteLater()

    welcome.projectChosen.connect(start)
    with loop:
        try:
            code = int(loop.run_forever())
        finally:
            if state["app"] is not None:
                state["app"].close_agent()  # while the loop is still open
    if state["app"] is not None:
        state["app"].close()
    return state["code"] or code


# -- relaunch (switch project) ------------------------------------------------------------
def relaunch_argv(root: Path | str, flags: list[str] | None = None) -> list[str]:
    """``python -m specto_app --project root [flags]`` with this app's interpreter, the
    console one under ``pythonw.exe`` (``console_python``, SPEC-28)."""
    python = console_python(sys.executable)
    return [python, "-m", "specto_app", "--project", str(root), *(flags or [])]


def relaunch(root: Path | str, flags: list[str] | None = None) -> int:
    """Start ``specto-app --project root`` detached from this process, the way ``specto
    open`` does (``specto_core.detached``: a hidden console, so the new app's agent and
    workers pop no console window, SPEC-28). Returns the pid."""
    argv = relaunch_argv(root, flags)
    log_path = recents.user_data_dir() / "launch.log"
    env = dict(os.environ)
    env.pop("SPECTO_PROJECT", None)
    pid = spawn_detached(argv, log_path=log_path, env=env)
    print(f"[OK] relaunching on {root} (pid {pid}); log {log_path}")
    return pid


__all__ = [
    "WELCOME_QML",
    "WelcomeController",
    "open_project",
    "relaunch",
    "relaunch_argv",
    "resolve_folder",
    "run_welcome",
]
