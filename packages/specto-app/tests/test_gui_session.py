"""tools/gui_session.py: a GUI session runs the app on a scratch user settings dir, so a
flow cannot change the user's own model choice, recents or trust list (#89); it drives the
desktop only while it holds the desktop lock, wipes only folders a session created, and
ends only the processes it started (SPEC-7)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[3] / "tools" / "gui_session.py"
_spec = importlib.util.spec_from_file_location("gui_session", _PATH)
assert _spec is not None and _spec.loader is not None
gui_session = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gui_session)
desktop_lock = gui_session.desktop_lock
gui_scratch = gui_session.gui_scratch


def test_the_app_gets_a_scratch_user_dir_inside_the_project(tmp_path):
    env = gui_session.app_env(tmp_path / "proj", {"PATH": "x"})
    assert env["SPECTO_USER_DIR"] == str(tmp_path / "proj" / ".specto" / "gui-user")
    assert env["PATH"] == "x"
    assert env["PYTHONUNBUFFERED"] == "1"


def test_an_explicit_user_dir_wins(tmp_path):
    env = gui_session.app_env(tmp_path / "proj", {"SPECTO_USER_DIR": str(tmp_path / "mine")})
    assert env["SPECTO_USER_DIR"] == str(tmp_path / "mine")


# -- the desktop lock --------------------------------------------------------------------------
@pytest.fixture
def lock(tmp_path, monkeypatch):
    path = tmp_path / "desktop.lock"
    monkeypatch.setattr(desktop_lock, "LOCK", path)
    monkeypatch.delenv(desktop_lock.ENV_NAME, raising=False)
    monkeypatch.delenv(desktop_lock.ENV_PRIVATE, raising=False)
    return path


def holder(path: Path) -> str | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("name")
    except OSError:
        return None


def test_a_session_takes_the_lock_itself_and_frees_it(lock):
    with desktop_lock.held("gui-session-1", wait_s=0):
        assert holder(lock) == "gui-session-1"
    assert not lock.exists()


def test_a_session_under_desktop_lock_run_uses_its_lock(lock, monkeypatch):
    assert desktop_lock.acquire("v2-mouse", 0)
    monkeypatch.setenv(desktop_lock.ENV_NAME, "v2-mouse")
    with desktop_lock.held("gui-session-1", wait_s=0):
        assert holder(lock) == "v2-mouse"
    assert holder(lock) == "v2-mouse"  # the run's to release, not the session's


def test_a_session_refuses_a_desktop_someone_else_holds(lock):
    assert desktop_lock.acquire("someone", 0)
    with pytest.raises(SystemExit, match="someone"):
        with desktop_lock.held("gui-session-1", wait_s=0):
            pytest.fail("drove the desktop without the lock")
    assert holder(lock) == "someone"


def test_a_private_display_needs_no_lock(lock, monkeypatch):
    assert desktop_lock.acquire("someone", 0)
    monkeypatch.setenv(desktop_lock.ENV_PRIVATE, "1")
    with desktop_lock.held("gui-session-1", wait_s=0):
        assert holder(lock) == "someone"


def test_desktop_lock_run_tells_its_command_the_lock_name(lock, tmp_path, monkeypatch):
    monkeypatch.setenv("SPECTO_DESKTOP_LOCK", str(lock))
    out = tmp_path / "env.txt"
    code = f"import os; open({str(out)!r}, 'w').write(os.environ[{desktop_lock.ENV_NAME!r}])"
    assert desktop_lock.run("v2-mouse", [sys.executable, "-c", code], 0) == 0
    assert out.read_text(encoding="utf-8") == "v2-mouse" and not lock.exists()


# -- fresh projects ----------------------------------------------------------------------------
def test_fresh_wipes_a_folder_a_session_created(tmp_path):
    project = tmp_path / "proj"
    gui_scratch.prepare_project(project, fresh=False)  # created here: marked
    (project / "left-over.txt").write_text("x", encoding="utf-8")
    gui_scratch.prepare_project(project, fresh=True)
    assert not (project / "left-over.txt").exists()
    assert gui_scratch.is_scratch(project)


def test_fresh_refuses_a_folder_with_content_no_session_created(tmp_path):
    project = tmp_path / "mine"
    project.mkdir()
    (project / "notes.md").write_text("keep me", encoding="utf-8")
    with pytest.raises(SystemExit, match="--fresh"):
        gui_scratch.prepare_project(project, fresh=True)
    assert (project / "notes.md").read_text(encoding="utf-8") == "keep me"
    gui_scratch.prepare_project(project, fresh=False)  # a plain session may use it
    assert not gui_scratch.is_scratch(project) and (project / "notes.md").exists()


def test_fresh_takes_an_empty_folder(tmp_path):
    project = tmp_path / "empty"
    project.mkdir()
    gui_scratch.prepare_project(project, fresh=True)
    assert gui_scratch.is_scratch(project)


def test_no_project_means_a_per_session_root(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_scratch.tempfile, "tempdir", str(tmp_path))
    a, b = gui_scratch.session_root(), gui_scratch.session_root()
    assert a != b and a.parent.parent == tmp_path
    assert gui_scratch.is_scratch(a) and gui_scratch.is_scratch(b)
    gui_scratch.remove_session_root(a)
    assert not a.parent.exists() and b.exists()


# -- cleanup ends only the session's own processes --------------------------------------------
SLEEPER = "import time; time.sleep(60)"
#: the app under ``uv run``: writes its pid (argv[1]) and waits
APP = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(60)"
#: a stand-in for ``uv run specto-app``: a parent that starts APP (argv[1]) as its child
SPAWNER = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); time.sleep(60)"
)


def wait_until(cond, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.1)
    return cond()


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill tree (the mouse tier's desktop)")
def test_kill_tree_ends_the_session_s_tree_and_nothing_else(tmp_path):
    from specto_core.instances import pid_alive

    other = subprocess.Popen([sys.executable, "-c", SLEEPER])  # another Specto, say
    pid_file = tmp_path / "child.pid"
    mine = subprocess.Popen([sys.executable, "-c", SPAWNER, APP, str(pid_file)])
    try:
        assert wait_until(lambda: pid_file.exists() and pid_file.read_text().strip())
        child = int(pid_file.read_text())
        assert pid_alive(child)
        gui_session.kill_tree(mine)
        mine.wait(10)
        assert wait_until(lambda: not pid_alive(child))
        assert pid_alive(other.pid) and other.poll() is None
    finally:
        for p in (other, mine):
            if p.poll() is None:
                p.kill()
