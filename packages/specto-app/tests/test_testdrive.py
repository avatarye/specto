"""The GUI test driver (#237). A normal launch never exposes it; ``--test-driver`` opens
it, and a run through it drives the real shell offscreen with Qt test events, swaps in the
fake clipboard and launchers, never raises the window, and leaves the desktop alone: the
foreground window, the cursor and the system clipboard are as they were, and the system
saw no input at all (``GetLastInputInfo``).

The driver runs in child processes (``specto-app`` itself), reached through
``tools/gui_qtdrive.py``'s channel, the way the flows reach it."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtQuick")

from specto_app import clipboard, testdrive
from specto_app.main import build_parser, passthrough_flags
from specto_core import Project

TOOLS = Path(__file__).resolve().parents[3] / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


qtdrive = _load("gui_qtdrive")


# -- a normal launch -----------------------------------------------------------------------
def test_the_flag_is_off_by_default_and_passed_through_a_relaunch():
    assert build_parser().parse_args([]).test_driver is False
    assert "--test-driver" not in passthrough_flags(build_parser().parse_args([]))
    assert "--test-driver" in passthrough_flags(build_parser().parse_args(["--test-driver"]))


def test_a_normal_launch_does_not_expose_the_driver(app):
    """The real shell, started the way the tests and the app start it: no driver, no
    channel file, the system clipboard."""
    assert testdrive.enabled() is False
    assert testdrive.driver() is None
    assert clipboard.is_fake() is False
    assert not (Path(os.environ["SPECTO_USER_DIR"]) / "drive.json").exists()


def test_a_normal_app_process_has_no_driver_channel(tmp_path):
    """A child ``specto-app`` without the flag: its endpoint comes up, no ``drive.json``
    appears and nothing listens on the driver's pipe name for its pid."""
    project = Project.init(tmp_path / "proj", "demo")
    user_dir = tmp_path / "user"
    env = _app_env(user_dir)
    proc = subprocess.Popen(
        [sys.executable, "-m", "specto_app", "--project", str(project.root), "--offscreen",
         "--inline-jobs", "--no-web"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )  # fmt: skip
    try:
        ep = _wait_for(project.root / ".specto" / "endpoint.json", proc)
        pid = json.loads(ep.read_text(encoding="utf-8"))["pid"]
        time.sleep(0.5)
        assert not (user_dir / "drive.json").exists()
        name = f"specto-drive-{pid}"
        address = f"\\\\.\\pipe\\{name}" if sys.platform == "win32" else name
        kind = "pipe" if sys.platform == "win32" else "unix"
        with pytest.raises((OSError, qtdrive.Flow)):
            qtdrive.Drive({"address": address, "kind": kind})
    finally:
        _end(proc)


# -- the driver ------------------------------------------------------------------------------
@pytest.fixture
def driven(tmp_path):
    """A child ``specto-app --test-driver`` on a small project, its driver channel and
    endpoint open; closed through the driver's ``quit``."""
    project = Project.init(tmp_path / "proj", "demo")
    (project.root / "notes").mkdir()
    (project.root / "notes" / "alpha.md").write_text("# alpha\n", encoding="utf-8")
    user_dir = tmp_path / "user"
    env = _app_env(user_dir)
    proc = subprocess.Popen(
        [sys.executable, "-m", "specto_app", "--project", str(project.root), "--test-driver",
         "--offscreen", "--inline-jobs", "--no-web"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )  # fmt: skip
    try:
        drive = qtdrive.open_drive(user_dir, proc)
        _wait_for(project.root / ".specto" / "endpoint.json", proc)
        time.sleep(0.3)
        ep = qtdrive.Endpoint(project.root)
        yield qtdrive.QtFlow(drive, project.root, tmp_path / "out", ep), user_dir
    finally:
        _end(proc, drive=locals().get("drive"))


def test_the_driver_locates_clicks_types_and_reads_the_fake_clipboard(driven):
    flow, user_dir = driven
    info = json.loads((user_dir / "drive.json").read_text(encoding="utf-8"))
    assert info["platform"] == "offscreen"
    assert flow.drive.call("ping")["attached"] is True
    flow.run("resize 1200 800")
    flow.run("wait_until treeRow:fege:notes/alpha.md 15000")
    # a hover reaches the status bar through the same hover handlers a mouse would
    flow.run("hover slot:fege 0.5 0.6")
    flow.run("wait 200")
    flow.run("assert_text statusHint hold Space and drag to pan")
    # a right-click opens the shell's context menu at the pointer; Copy path writes the
    # driver's clipboard, which assert_clipboard reads back
    flow.run("rclick treeRow:fege:notes/alpha.md 0.6 0.5")
    flow.run("wait_until menu:copy.path 5000")
    flow.run("wait 400")  # the rows are laid out a frame later (the flows wait too)
    flow.run("click menu:copy.path")
    flow.run("wait_until menu:copy.path 5000".replace("wait_until", "wait_while"))
    flow.run("assert_clipboard {project}\\notes\\alpha.md")
    # keys reach the shell with no OS focus anywhere: Ctrl+Z undoes the header drag
    flow.run("mark slot:fege")
    flow.run("dragxy header:fege 80 40 0.3 0.5")
    flow.run("wait 300")
    flow.run("assert_moved slot:fege 80 40 4")
    flow.run("click canvasArea 0.9 0.9")
    flow.run("key ctrl+z")
    flow.run("wait 600")
    flow.run("assert_moved slot:fege 0 0 4")
    # a grab of the window lands as a PNG
    flow.run("shot drive.png")
    assert (flow.out / "drive.png").stat().st_size > 1000
    # window.raise is a no-op under the driver (it reports raised, raises nothing)
    assert flow.ep.call("window.raise", {})["raised"] is True
    # launchers started nothing real: the fake log holds what was asked
    log = user_dir / "launches.jsonl"
    flow.run("rclick treeRow:fege:notes/alpha.md 0.6 0.5")
    flow.run("wait_until menu:launch.default 5000")
    flow.run("wait 400")
    flow.run("click menu:launch.default")
    flow.run("wait 300")
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert entries and entries[-1]["kind"] == "start_file"
    assert entries[-1]["path"].endswith("alpha.md")


def test_window_flags_take_the_focus_away_only_off_the_offscreen_platform(monkeypatch):
    from PySide6.QtCore import Qt

    plain = Qt.WindowType.Window
    monkeypatch.setattr(testdrive, "_enabled", True)
    monkeypatch.setattr(testdrive, "offscreen", lambda: True)
    assert testdrive.window_flags(plain) == plain
    monkeypatch.setattr(testdrive, "offscreen", lambda: False)
    assert testdrive.window_flags(plain) & Qt.WindowType.WindowDoesNotAcceptFocus
    monkeypatch.setattr(testdrive, "_enabled", False)
    assert testdrive.window_flags(plain) == plain


def test_a_shown_window_is_lowered_behind_the_users_work(qapp, tmp_path, monkeypatch):
    """Off the offscreen platform the driver lowers every top-level window as it shows and
    on its first expose (#34): ``WindowDoesNotAcceptFocus`` alone leaves a new window on
    top of the z order, over what the user looks at. The rule, on a window that records
    ``lower``; popups and child windows are left alone."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QWindow

    calls: list[str] = []

    class Recording(QWindow):
        def lower(self) -> None:
            calls.append(self.title())

    monkeypatch.setattr(testdrive, "user_data_dir", lambda: tmp_path)
    drv = testdrive.TestDriver()
    try:
        win = Recording()
        win.setTitle("shell")
        drv.eventFilter(win, QEvent(QEvent.Type.Show))
        drv.eventFilter(win, QEvent(QEvent.Type.Expose))
        drv.eventFilter(win, QEvent(QEvent.Type.Expose))
        assert calls == ["shell", "shell"], "once on show, once on the first expose"
        popup = Recording()
        popup.setTitle("menu")
        popup.setFlags(Qt.WindowType.Popup)
        drv.eventFilter(popup, QEvent(QEvent.Type.Show))
        child = Recording(win)
        child.setTitle("child")
        drv.eventFilter(child, QEvent(QEvent.Type.Show))
        assert calls == ["shell", "shell"]
    finally:
        drv.server.close()


def test_key_names_follow_the_flows():
    from PySide6.QtCore import Qt

    assert testdrive.parse_key("ctrl+z") == (Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier, "")
    assert testdrive.parse_key("shift+f10") == (
        Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier, ""
    )  # fmt: skip
    assert testdrive.parse_key("`")[0] == Qt.Key.Key_QuoteLeft
    assert testdrive.parse_key("ctrl+-")[0] == Qt.Key.Key_Minus
    assert testdrive.parse_key("w") == (Qt.Key.Key_W, Qt.KeyboardModifier.NoModifier, "w")
    assert testdrive.parse_key("esc")[0] == Qt.Key.Key_Escape
    # a modifier held on its own, what ``dragxy ... mod=alt`` presses first (#34)
    assert testdrive.parse_key("alt") == (Qt.Key.Key_Alt, Qt.KeyboardModifier.NoModifier, "")
    assert testdrive.parse_key("ctrl")[0] == Qt.Key.Key_Control


# -- the desktop is left alone ---------------------------------------------------------------
FLOW = """\
resize 1200 800
wait_until treeRow:fege:notes/alpha.md 15000
hover treeRow:fege:notes/alpha.md
click treeRow:fege:notes/alpha.md
rclick treeRow:fege:notes/alpha.md 0.6 0.5
wait_until menu:copy.path 5000
wait 400
click menu:copy.path
wait 200
assert_clipboard {project}\\notes\\alpha.md
dragxy header:fege 60 40 0.3 0.5
key home
hover agentIcon
wait 400
type hello
shot side-effects.png
"""


@pytest.mark.skipif(sys.platform != "win32", reason="reads the Windows desktop state")
def test_a_driver_run_leaves_foreground_cursor_and_clipboard_unchanged(tmp_path):
    """A whole session through ``gui_qtdrive`` (clicks, a drag, hovers, keys, a Copy
    path): the foreground window, the cursor position and the system clipboard read the
    same after as before. Injected mouse input would move the cursor and a real Copy
    would change the clipboard; a user moving the mouse or switching windows meanwhile
    cannot be told apart, so such a run is repeated (typing changes none of the three).
    On an idle desktop the stronger form holds too and is reported: Windows registered
    no input at all during the run (``GetLastInputInfo`` moves for every real or
    injected mouse or key event, never for Qt's in-process test events)."""
    import ctypes
    import ctypes.wintypes as wt

    platform = _load("gui_platform")
    user32 = ctypes.windll.user32

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]

    def last_input() -> int:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        assert user32.GetLastInputInfo(ctypes.byref(lii))
        return int(lii.dwTime)

    def desktop() -> tuple[int, tuple[int, int], str]:
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return int(user32.GetForegroundWindow()), (pt.x, pt.y), platform.clipboard_text()

    project = Project.init(tmp_path / "proj", "demo")
    (project.root / "notes").mkdir()
    (project.root / "notes" / "alpha.md").write_text("# alpha\n", encoding="utf-8")
    flow_file = tmp_path / "side-effects.txt"
    flow_file.write_text(FLOW, encoding="utf-8")
    for attempt in range(6):
        ns = argparse.Namespace(
            project=project.root, out=tmp_path / f"out{attempt}", fresh=False,
            app_log=tmp_path / f"app{attempt}.log", keep=False, pool=False, extra="--no-web",
            welcome=False, shot_prefix="x", welcome_recent=False, welcome_only=False,
            flows=[flow_file],
        )  # fmt: skip
        before_input = last_input()
        before = desktop()
        results = qtdrive.run_session(ns)
        after = desktop()
        after_input = last_input()
        assert all(r["ok"] for r in results), results
        assert after[2] == before[2], f"the clipboard changed: {before[2]!r} -> {after[2]!r}"
        if after[:2] != before[:2] and after_input != before_input:
            continue  # the user moved the mouse or switched windows: nothing can be told
        assert after == before, f"the desktop changed: {before} -> {after}"
        idle = "idle desktop: no input registered" if after_input == before_input else "user typed"
        print(f"[OK] foreground, cursor and clipboard unchanged ({idle})")
        return
    pytest.fail("the mouse or the foreground moved during every attempt; nothing can be told")


# -- helpers -----------------------------------------------------------------------------------
def _app_env(user_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "SPECTO_USER_DIR": str(user_dir),
            "QT_QPA_PLATFORM": "offscreen",
            "QT_QUICK_BACKEND": "software",
            "PYTHONUNBUFFERED": "1",
            "SPECTO_DISABLE_CAPABILITIES": "ovrtx,storm",
        }
    )
    return env


def _wait_for(path: Path, proc: subprocess.Popen, timeout: float = 60.0) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return path
        assert proc.poll() is None, f"the app exited early with {proc.returncode}"
        time.sleep(0.1)
    raise AssertionError(f"{path} never appeared")


def _end(proc: subprocess.Popen, drive=None) -> None:
    if drive is not None and proc.poll() is None:
        try:
            drive.call("quit")
            proc.wait(10)
        except Exception:
            pass
    if proc.poll() is None:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
        proc.wait(10)
