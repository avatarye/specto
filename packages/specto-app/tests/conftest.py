"""Headless fixtures: the offscreen platform with the software Qt Quick backend, one
``QGuiApplication`` per process, and a started ``SpectoApp`` on a temp project."""

from __future__ import annotations

import gc
import os
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QSG_RHI_BACKEND", "opengl")
# recents and GUI settings of the test processes never touch the user's own
_USER_DIR = tempfile.mkdtemp(prefix="specto-tests-user-")
os.environ["SPECTO_USER_DIR"] = _USER_DIR
# usd views run the stub USD source, not RTX or Storm, even where this machine has them;
# the rtx tests pass the engine their own capabilities (#137)
os.environ.setdefault("SPECTO_DISABLE_CAPABILITIES", "ovrtx,storm")

from specto_core import Project  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PySide6.QtQuick")
    from PySide6.QtGui import QGuiApplication

    from specto_app.qt_setup import prepare

    prepare(web=False, offscreen=True)
    app = QGuiApplication.instance() or QGuiApplication(["specto-tests"])
    yield app
    # a QMimeData made in Python and still on the clipboard when the process exits
    # segfaults the interpreter's teardown (#172): drop it while the app is alive
    app.clipboard().clear()


@pytest.fixture(autouse=True)
def _no_real_programs(monkeypatch):
    """Launchers (#186) never open a real program from a test: the system default,
    Explorer and the editor do nothing, and a launch may start this Python alone (the
    fake launcher that records its argv)."""
    from specto_app.launchers import run

    real_spawn = run.spawn_program
    me = os.path.normcase(os.path.abspath(sys.executable))

    def spawn(argv, cwd="", env=None):
        if os.path.normcase(os.path.abspath(argv[0])) != me:
            raise OSError(f"a test may launch only {sys.executable}, not {argv[0]}")
        return real_spawn(argv, cwd, env)

    monkeypatch.setattr(run, "spawn_program", spawn)
    monkeypatch.setattr(run, "start_file", lambda path: None)
    monkeypatch.setattr(run, "reveal", lambda paths: None)
    monkeypatch.setattr(run, "edit_file", lambda path: None)


@pytest.fixture(autouse=True)
def _collect_between_tests():
    """Whatever a test left in a reference cycle is collected here, outside every Qt
    loop, never by the cyclic GC inside a later test's ``processEvents`` (QObjects freed
    there in an arbitrary order were the suite's intermittent access violation)."""
    yield
    gc.collect()


@pytest.fixture
def spin(qapp) -> Callable[..., None]:
    def _spin(ms: float = 60, until: Callable[[], bool] | None = None, timeout: float = 5.0):
        deadline = time.monotonic() + (timeout if until else ms / 1000.0)
        while time.monotonic() < deadline:
            qapp.processEvents()
            if until is not None and until():
                break
            time.sleep(0.004)
        qapp.processEvents()
        if until is not None:
            assert until(), "timed out"

    return _spin


@pytest.fixture(scope="session")
def _asyncio_loop():
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def aloop(_asyncio_loop):
    """The asyncio loop the prompt bar's agent runs on. A plain loop stepped by hand
    (``aspin``), not the app's qasync one: a nested Qt ``exec()`` would flush the
    deferred deletes of every app torn down before in this process. qasync itself is
    exercised by the real app (the scripted GUI flow and the live smoke)."""
    import asyncio

    asyncio.set_event_loop(_asyncio_loop)
    return _asyncio_loop


@pytest.fixture
def aspin(aloop, qapp) -> Callable[..., None]:
    """Step the asyncio loop and the Qt loop until a condition holds (the agent's turn
    finished)."""
    import asyncio

    def _aspin(until: Callable[[], bool], timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            aloop.run_until_complete(asyncio.sleep(0.01))
            qapp.processEvents()
            if until():
                return
        assert until(), "timed out"

    return _aspin


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.init(tmp_path / "proj", "demo")


@pytest.fixture
def app(qapp, project, spin):
    import shiboken6

    from specto_app.app import SpectoApp

    a = SpectoApp(project, jobs="inline", web=False, offscreen=True, serve=True)
    a.start()
    spin(50)
    yield a
    qml, window = a.qml, a.window
    a.close()
    spin(20)
    # the shell dies in close(), never later in another test's Qt loop (cyclic GC)
    assert not shiboken6.isValid(qml) and not shiboken6.isValid(window), "shell outlived close()"
