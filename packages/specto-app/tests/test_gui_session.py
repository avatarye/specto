"""tools/gui_session.py: a GUI session runs the app on a scratch user settings dir, so a
flow cannot change the user's own model choice, recents or trust list (#89); it drives the
desktop only while it holds the desktop lock, wipes only folders a session created, and
ends only the processes it started (SPEC-7)."""

from __future__ import annotations

import importlib.util
import json
import os
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
    monkeypatch.setenv("SPECTO_DESKTOP_LOCK", str(path))  # for child processes
    monkeypatch.delenv(desktop_lock.ENV_NAME, raising=False)
    monkeypatch.delenv(desktop_lock.ENV_PRIVATE, raising=False)
    return path


#: a process that holds the desktop lock until ``argv[2]`` appears, saying so in ``argv[1]``
HOLDER = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[3])
import desktop_lock
f = desktop_lock.acquire("holder", 30)
Path(sys.argv[1]).write_text("held" if f else "no")
deadline = time.monotonic() + 60
while not Path(sys.argv[2]).exists() and time.monotonic() < deadline:
    time.sleep(0.05)
"""


@pytest.fixture
def holder_process(lock, tmp_path):
    """Start a process holding the lock; returns ``(proc, release)``."""
    started, done = tmp_path / "held.txt", tmp_path / "done.txt"
    procs: list[subprocess.Popen] = []

    def start() -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(started), str(done), str(_PATH.parent)]
        )
        procs.append(proc)
        assert wait_until(lambda: started.exists())
        assert started.read_text() == "held"
        return proc

    yield start, (lambda: done.write_text("x"))
    for proc in procs:
        if proc.poll() is None:
            proc.kill()


def test_a_session_takes_the_lock_itself_and_frees_it(lock):
    with desktop_lock.held("gui-session-1", wait_s=0):
        assert not desktop_lock.is_free()
        assert desktop_lock.holder_name().startswith("gui-session-1 (pid ")
        assert os.environ[desktop_lock.ENV_NAME] == "gui-session-1"  # for its children
    assert desktop_lock.is_free() and desktop_lock.ENV_NAME not in os.environ


def test_a_second_process_waits_and_gets_the_lock_when_the_first_exits(lock, holder_process):
    start, release = holder_process
    proc = start()
    assert desktop_lock.acquire("me", 0) is None
    with pytest.raises(SystemExit, match="holder"):
        with desktop_lock.held("me", wait_s=0):
            pytest.fail("drove the desktop without the lock")
    release()
    proc.wait(30)
    f = desktop_lock.acquire("me", 10)
    assert f is not None
    desktop_lock.release(f, "me")


def test_a_killed_holder_frees_the_lock_at_once(lock, holder_process):
    start, _ = holder_process
    proc = start()
    proc.kill()
    proc.wait(30)
    f = desktop_lock.acquire("me", 2)  # no staleness to wait out
    assert f is not None
    desktop_lock.release(f, "me")


def test_the_sidecar_never_decides(lock):
    """It names a live holder (this process), but the OS lock is free: the lock is taken."""
    desktop_lock.sidecar().write_text(
        json.dumps({"name": "ghost", "pid": os.getpid(), "since": 1.0}), encoding="utf-8"
    )
    assert desktop_lock.is_free()
    f = desktop_lock.acquire("me", 0)
    assert f is not None and desktop_lock.holder_name().startswith("me ")
    desktop_lock.release(f, "me")


def test_threads_of_one_process_share_one_hold(lock):
    import threading

    inside, leave = threading.Event(), threading.Event()

    def worker() -> None:
        with desktop_lock.held("worker", wait_s=5):
            inside.set()
            leave.wait(10)

    with desktop_lock.held("main", wait_s=0):
        thread = threading.Thread(target=worker)
        thread.start()
        assert inside.wait(10)  # it shared the hold instead of waiting for it
    assert not desktop_lock.is_free()  # the worker still holds it
    leave.set()
    thread.join(10)
    assert desktop_lock.is_free()


def test_a_child_of_a_holder_passes_through(lock, monkeypatch):
    f = desktop_lock.acquire("v2-mouse", 0)
    try:
        monkeypatch.setenv(desktop_lock.ENV_NAME, "v2-mouse")
        with desktop_lock.held("gui-session-1", wait_s=0):  # would wait on its parent
            assert desktop_lock.holder_name().startswith("v2-mouse ")
    finally:
        desktop_lock.release(f, "v2-mouse")


def test_a_private_display_needs_no_lock(lock, holder_process, monkeypatch):
    start, _ = holder_process
    start()
    monkeypatch.setenv(desktop_lock.ENV_PRIVATE, "1")
    with desktop_lock.held("gui-session-1", wait_s=0):
        pass


def test_desktop_lock_run_tells_its_command_the_lock_name(lock, tmp_path):
    out = tmp_path / "env.txt"
    code = f"import os; open({str(out)!r}, 'w').write(os.environ[{desktop_lock.ENV_NAME!r}])"
    assert desktop_lock.run("v2-mouse", [sys.executable, "-c", code], 0) == 0
    assert out.read_text(encoding="utf-8") == "v2-mouse" and desktop_lock.is_free()


def test_the_tools_pid_alive_matches_the_core_one():
    from specto_core.instances import pid_alive as core_pid_alive

    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait(30)
    for pid in (os.getpid(), done.pid, 0, -1):
        assert gui_session.gui_scratch.pid_alive(pid) == core_pid_alive(pid), pid


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


def test_fresh_uses_someone_s_empty_folder_but_never_marks_it(tmp_path):
    """An existing folder without a session's mark is never made wipeable."""
    project = tmp_path / "empty"
    project.mkdir()
    assert gui_scratch.prepare_project(project, fresh=True) is False
    assert project.is_dir() and not gui_scratch.is_scratch(project)
    (project / "notes.md").write_text("theirs", encoding="utf-8")
    with pytest.raises(SystemExit):
        gui_scratch.prepare_project(project, fresh=True)
    assert (project / "notes.md").exists()


def test_a_copied_marked_folder_is_not_the_copy_s_own(tmp_path):
    """The mark names its folder's resolved path: a copy (or a move) of a session's
    folder carries a mark that does not name it, so --fresh refuses it."""
    import shutil

    original = tmp_path / "proj"
    gui_scratch.prepare_project(original, fresh=False)
    (original / "data.txt").write_text("x", encoding="utf-8")
    copy = tmp_path / "copy"
    shutil.copytree(original, copy)
    assert gui_scratch.is_scratch(original) and not gui_scratch.is_scratch(copy)
    with pytest.raises(SystemExit):
        gui_scratch.prepare_project(copy, fresh=True)
    assert (copy / "data.txt").exists()
    record = gui_scratch.owner(original)
    assert record.pid == os.getpid() and record.nonce and record.kept is False


def test_fresh_waits_for_the_lock_before_it_wipes(tmp_path, lock, holder_process, monkeypatch):
    """--fresh runs under the desktop lock: while another session holds it, the folder
    it may be using is not touched."""
    project = tmp_path / "proj"
    gui_scratch.prepare_project(project, fresh=False)
    (project / "in-use.txt").write_text("x", encoding="utf-8")
    holder_process[0]()
    argv = ["gui_session.py", "--project", str(project), "--out", str(tmp_path / "out")]
    monkeypatch.setattr(sys, "argv", [*argv, "--fresh", "--lock-wait", "0"])
    assert gui_session.main() == 2
    assert (project / "in-use.txt").exists()


def test_old_session_roots_whose_creator_is_gone_are_swept(tmp_path, monkeypatch):
    """Swept: old, creator gone, not kept. Kept: a live creator, a young root, and a root
    left open with its app (--keep), whose app may still run."""
    from dataclasses import asdict, replace

    monkeypatch.setattr(gui_scratch.tempfile, "tempdir", str(tmp_path))
    old, mine, young = (gui_scratch.session_root() for _ in range(3))
    kept_gone, kept_open, kept_unknown = (gui_scratch.session_root(kept=True) for _ in range(3))
    old_age = 2 * 86400
    cases = (
        (old, 0, None, old_age),
        (mine, os.getpid(), None, old_age),
        (young, 0, None, 60),
        (kept_gone, 0, 0, old_age),  # its app is gone too: swept
        (kept_open, 0, os.getpid(), old_age),  # its app still runs
        (kept_unknown, 0, None, old_age),  # its app never recorded: left to the user
    )
    for project, pid, app_pid, age in cases:
        record = replace(
            gui_scratch.owner(project), pid=pid, app_pid=app_pid, created=time.time() - age
        )
        (project / gui_scratch.MARKER).write_text(json.dumps(asdict(record)), encoding="utf-8")
    swept = gui_scratch.sweep_session_roots()
    assert sorted(swept) == sorted([old.parent, kept_gone.parent])
    assert all(p.exists() for p in (mine, young, kept_open, kept_unknown))


def test_the_suite_wipes_only_its_own_entry_folders(tmp_path):
    """v2_suite.run_entry reseeds an entry's folder only when a session made it (or it is
    empty); a folder of someone else's under the same name is left alone."""
    spec = importlib.util.spec_from_file_location(
        "v2_suite_t", _PATH.parent / "gui_flows" / "v2_suite.py"
    )
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    entry = {"seed": lambda project: (project / "seeded.txt").write_text("s"), "sessions": []}
    work = tmp_path / "work"
    foreign = work / "v49-x"
    foreign.mkdir(parents=True)
    (foreign / "mine.txt").write_text("keep", encoding="utf-8")
    (result,) = suite.run_entry("x", entry, work, tmp_path / "out")
    assert not result["ok"] and "delete" in result["error"]
    assert (foreign / "mine.txt").read_text(encoding="utf-8") == "keep"
    import shutil

    shutil.rmtree(foreign)  # deleted once by hand: the suite creates it, and owns it
    assert suite.run_entry("x", entry, work, tmp_path / "out") == []
    assert gui_scratch.is_scratch(foreign)
    (foreign / "stale.txt").write_text("from the last run", encoding="utf-8")
    assert suite.run_entry("x", entry, work, tmp_path / "out") == []
    assert not (foreign / "stale.txt").exists() and (foreign / "seeded.txt").exists()


def test_the_suite_and_qtdrive_reseed_under_the_desktop_lock(tmp_path, monkeypatch):
    """A wipe never touches a folder a mouse-tier session holding the lock is using: the
    suite's reseed and gui_qtdrive's --fresh both run inside desktop_lock.held."""
    import contextlib

    spec = importlib.util.spec_from_file_location(
        "v2_suite_t2", _PATH.parent / "gui_flows" / "v2_suite.py"
    )
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    events: list[str] = []

    @contextlib.contextmanager
    def held(name, wait_s=3600.0):
        events.append(f"lock {name.split('-')[0]}")
        yield
        events.append("unlock")

    real_prepare = gui_scratch.prepare_project

    def prepare(project, *, fresh):
        events.append(f"prepare fresh={fresh}")
        return real_prepare(project, fresh=fresh)

    monkeypatch.setattr(suite.desktop_lock, "held", held)
    monkeypatch.setattr(suite.gui_scratch, "prepare_project", prepare)
    entry = {"seed": lambda project: events.append("seed"), "sessions": []}
    assert suite.run_entry("x", entry, tmp_path / "work", tmp_path / "out") == []
    assert events == ["lock v2", "prepare fresh=True", "seed", "unlock"]  # the seed too
    src = (_PATH.parent / "gui_qtdrive.py").read_text(encoding="utf-8")
    assert "gui_scratch.wipe_fresh(" in src.split("def run_session", 1)[1].split("ep_path", 1)[0]


def test_the_suite_never_seeds_someone_s_empty_folder(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "v2_suite_t3", _PATH.parent / "gui_flows" / "v2_suite.py"
    )
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    seeded: list[Path] = []
    entry = {"seed": seeded.append, "sessions": []}
    theirs = tmp_path / "work" / "v49-x"
    theirs.mkdir(parents=True)
    (result,) = suite.run_entry("x", entry, tmp_path / "work", tmp_path / "out")
    assert not result["ok"] and seeded == [] and not gui_scratch.is_scratch(theirs)


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
