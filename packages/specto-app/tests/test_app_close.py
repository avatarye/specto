"""Closing the window ends the process (#118): ``run()`` returns and the interpreter exits
within a few seconds, and the agent session's child process (the Claude CLI) is gone.
A session left running kept pipe reads pending on the asyncio proactor, whose close then
waited for them forever: the window was gone and the process stayed.

Each case runs the app in a child process (``app_close_child.py``): a hang must not
take the test session with it."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtQuick")

CHILD = Path(__file__).with_name("app_close_child.py")
#: from the window closing to the process having exited
EXIT_S = 5.0


def alive(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            import os

            os.kill(pid, 0)
        except OSError:
            return False
        return True
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259
    finally:
        k32.CloseHandle(h)


def kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        import os
        import signal

        os.kill(pid, signal.SIGKILL)


def run_child(project: Path, *args: str) -> tuple[list[tuple[float, str]], float | None, int]:
    """Run the child; return its timestamped lines, the seconds from ``CLOSING`` to its
    exit (None: it did not exit in time) and its return code."""
    proc = subprocess.Popen(
        [sys.executable, str(CHILD), str(project), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: list[tuple[float, str]] = []

    def read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append((time.monotonic(), line.rstrip()))

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + 60
    closing: float | None = None
    while time.monotonic() < deadline:
        closing = next((t for t, s in lines if s == "CLOSING"), None)
        if proc.poll() is not None or (closing and time.monotonic() - closing > EXIT_S):
            break
        time.sleep(0.05)
    exited = proc.poll() is not None
    took = time.monotonic() - closing if exited and closing else None
    if not exited:
        kill_tree(proc.pid)
        proc.wait(10)
    reader.join(5)
    return lines, took, proc.returncode


def agent_pid(lines: list[tuple[float, str]]) -> int:
    pids = [int(s.split()[1]) for _, s in lines if s.startswith("AGENT ")]
    assert pids, "the agent session never started:\n" + "\n".join(s for _, s in lines)
    return pids[0]


@pytest.mark.parametrize("when", ["after a turn", "mid-turn"])
def test_closing_the_window_ends_the_process_and_the_agent_session(tmp_path, when):
    args = ("--mid-turn",) if when == "mid-turn" else ()
    lines, took, code = run_child(tmp_path / "proj", *args)
    out = "\n".join(s for _, s in lines)
    pid = agent_pid(lines)
    try:
        assert took is not None, f"the process was still running {EXIT_S} s after close:\n{out}"
        assert code == 0, out
        assert "RUN RETURNED" in out
        assert not alive(pid), "the agent session's process outlived the app"
    finally:
        if alive(pid):
            kill_tree(pid)
