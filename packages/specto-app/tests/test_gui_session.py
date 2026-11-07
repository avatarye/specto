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
        name = holder(lock)
        assert name.startswith("gui-session-1-") and len(name) > len("gui-session-1-")  # nonce
    assert not lock.exists()


def test_an_unreadable_lock_is_busy_until_it_has_not_changed_for_the_stale_time(lock):
    """A holder mid-write leaves an empty or torn file for an instant: a waiter never
    deletes it then. Only one left unchanged for STALE_S is taken over."""
    import os

    lock.write_text('{"name": "some', encoding="utf-8")
    assert not desktop_lock.acquire("me", 0)
    assert lock.read_text(encoding="utf-8") == '{"name": "some'
    old = time.time() - desktop_lock.STALE_S - 60
    os.utime(lock, (old, old))
    assert desktop_lock.acquire("me", 0) and holder(lock) == "me"


def test_a_lock_whose_holder_is_gone_is_stale(lock):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(30)
    now = time.time()
    lock.write_text(
        json.dumps({"name": "crashed", "pid": gone.pid, "since": now, "beat": now}),
        encoding="utf-8",
    )
    assert desktop_lock.acquire("me", 0) and holder(lock) == "me"


def test_the_lock_appears_whole_and_leaves_nothing_behind(lock):
    assert desktop_lock.acquire("me", 0)
    assert desktop_lock.read().name == "me" and desktop_lock.read().pid == os.getpid()
    assert not [f for f in lock.parent.iterdir() if f != lock]  # no temp or aside files
    desktop_lock.release("me")
    assert not lock.exists() and not list(lock.parent.iterdir())


def test_two_takers_of_one_stale_lock_end_with_one_holder(lock):
    """Both judged the same stale lock. The first moves it aside and puts its own in
    place; the second's takeover moves the first's fresh lock aside, sees it is not the
    one it judged, and puts it back: one holder, never two."""
    now = time.time()
    stale = json.dumps({"name": "crashed", "pid": 0, "since": now})
    lock.write_text(stale, encoding="utf-8")
    judged = lock.read_text(encoding="utf-8")
    assert desktop_lock.stale_reason(desktop_lock.LockInfo.parse(judged), now)
    assert desktop_lock.take_over(judged) and desktop_lock._create("first")
    assert not desktop_lock.take_over(judged)  # the second taker, late with its judgement
    assert holder(lock) == "first"
    assert not desktop_lock._create("second")
    assert not [f for f in lock.parent.iterdir() if f != lock]


def test_a_release_leaves_a_lock_that_is_no_longer_its_own(lock):
    assert desktop_lock.acquire("me", 0)
    lock.write_text(json.dumps({"name": "next", "pid": os.getpid(), "since": 1.0}), "utf-8")
    desktop_lock.release("me")
    assert holder(lock) == "next"


def test_a_failing_beat_says_so_and_keeps_beating(lock, monkeypatch, capsys):
    monkeypatch.setattr(desktop_lock, "BEAT_S", 0.05)
    assert desktop_lock.acquire("me", 0)
    calls = {"n": 0}
    real_utime = os.utime

    def flaky(path, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("held open")
        return real_utime(path, *a, **kw)

    monkeypatch.setattr(desktop_lock.os, "utime", flaky)
    stop = desktop_lock._beating("me")
    deadline = time.monotonic() + 5
    while calls["n"] < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    stop.set()
    assert calls["n"] >= 3  # it went on after the failure
    assert "beat for me failed" in capsys.readouterr().out


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


def test_fresh_waits_for_the_lock_before_it_wipes(tmp_path, lock, monkeypatch):
    """--fresh runs under the desktop lock: while another session holds it, the folder
    it may be using is not touched."""
    project = tmp_path / "proj"
    gui_scratch.prepare_project(project, fresh=False)
    (project / "in-use.txt").write_text("x", encoding="utf-8")
    assert desktop_lock.acquire("someone", 0)
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
    kept = gui_scratch.session_root(kept=True)
    cases = ((old, 0, 2 * 86400), (mine, os.getpid(), 2 * 86400), (young, 0, 60))
    for project, pid, age in (*cases, (kept, 0, 2 * 86400)):
        record = replace(gui_scratch.owner(project), pid=pid, created=time.time() - age)
        (project / gui_scratch.MARKER).write_text(json.dumps(asdict(record)), encoding="utf-8")
    assert gui_scratch.sweep_session_roots() == [old.parent]
    assert not old.parent.exists() and mine.exists() and young.exists() and kept.exists()


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
    entry = {"seed": lambda project: None, "sessions": []}
    assert suite.run_entry("x", entry, tmp_path / "work", tmp_path / "out") == []
    assert events == ["lock v2", "prepare fresh=True", "unlock"]
    src = (_PATH.parent / "gui_qtdrive.py").read_text(encoding="utf-8")
    assert "with desktop_lock.held(" in src.split("def run_session", 1)[1].split("ep_path", 1)[0]


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
