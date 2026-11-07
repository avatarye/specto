"""``specto_core.detached`` (SPEC-28): a detached child logs, outlives nothing it should
not, and on Windows its own console children get a hidden console, not a visible one."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from specto_core import detached

#: what the helper must never pass (a console-less child)
DETACHED_PROCESS = 0x00000008

#: the grandchild: started by the detached child with no flags, as the Agent SDK starts
#: ``claude`` and the worker pool its interpreters; it reports the console it got
GRANDCHILD = textwrap.dedent(
    """
    import ctypes, json, sys
    k32 = ctypes.WinDLL("kernel32")
    hwnd = k32.GetConsoleWindow()  # NULL for a windowless console
    visible = bool(hwnd) and bool(ctypes.WinDLL("user32").IsWindowVisible(hwnd))
    attached = k32.GetConsoleCP() != 0  # 0: no console at all
    open(sys.argv[1], "w").write(json.dumps({"console": attached, "visible": visible}))
    """
)
CHILD = textwrap.dedent(
    """
    import subprocess, sys
    print("child up", flush=True)
    subprocess.run([sys.executable, "-c", sys.argv[1], sys.argv[2]], check=True)
    """
)


def _wait_for(path: Path, timeout: float = 30.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            time.sleep(0.05)
            return path.read_text(encoding="utf-8")
        time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows consoles")
def test_a_console_grandchild_gets_a_hidden_console(tmp_path: Path):
    report = tmp_path / "report.json"
    log = tmp_path / "logs" / "launch.log"
    pid = detached.spawn_detached(
        [sys.executable, "-c", CHILD, GRANDCHILD, str(report)], log_path=log, cwd=tmp_path
    )
    assert pid > 0
    # it inherited the child's console (so it allocated none of its own), and that
    # console shows no window
    got = json.loads(_wait_for(report))
    assert got == {"console": True, "visible": False}
    assert "child up" in _wait_for(log)


def test_output_goes_to_the_log_or_nowhere(tmp_path: Path):
    log = tmp_path / "out.log"
    log.write_bytes(b"earlier\n")
    detached.spawn_detached([sys.executable, "-c", "print('hello')"], log_path=log)
    deadline = time.monotonic() + 30
    while "hello" not in log.read_text(encoding="utf-8") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert log.read_text(encoding="utf-8").splitlines()[:2] == ["earlier", "hello"]  # appended
    # no log: the output is discarded; ``env`` is the child's whole environment
    marker = tmp_path / "ran"
    code = f"import os; open({str(marker)!r}, 'w').write(os.environ['SPECTO_DETACHED_T'])"
    env = {**os.environ, "SPECTO_DETACHED_T": "seen"}
    detached.spawn_detached([sys.executable, "-c", code], env=env)
    assert _wait_for(marker) == "seen"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process creation flags")
def test_falls_back_when_the_job_forbids_breaking_away(monkeypatch):
    seen: list[int] = []

    class FakePopen:
        pid = 7
        _handle = None
        returncode = None

        def __init__(self, argv, *, creationflags, **kw):
            seen.append(creationflags)
            if creationflags & detached.CREATE_BREAKAWAY_FROM_JOB:
                raise PermissionError("access denied")

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    assert detached.spawn_detached(["x"]) == 7
    base = detached.CREATE_NEW_PROCESS_GROUP | detached.CREATE_NO_WINDOW
    assert seen == [base | detached.CREATE_BREAKAWAY_FROM_JOB, base]
    assert not any(f & DETACHED_PROCESS for f in seen)
