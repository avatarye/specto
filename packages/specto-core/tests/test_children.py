"""children: approved code runs in a child that cannot outlive, flood or read its caller's
environment (ADR 0006's code-running paths)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from specto_core.children import MINIMAL_ENV_KEYS, minimal_env, run_child


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_a_timeout_kills_the_child_and_what_it_started(tmp_path: Path):
    pidfile = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open(r'{pidfile}', 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    run = run_child(
        [sys.executable, "-c", script], b"", timeout=3.0, out_cap=1 << 20, err_cap=1 << 20
    )
    assert run.timed_out
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _alive(pid)


def test_output_past_the_cap_fails_the_run():
    script = "import sys; sys.stdout.write('x' * 300000); sys.stdout.flush()"
    run = run_child(
        [sys.executable, "-c", script], b"", timeout=30.0, out_cap=100000, err_cap=1 << 20
    )
    assert run.over_cap == "stdout"
    ok = run_child(
        [sys.executable, "-c", script], b"", timeout=30.0, out_cap=1 << 20, err_cap=1 << 20
    )
    assert ok.over_cap is None and len(ok.stdout) == 300000


def test_the_minimal_environment_carries_only_what_an_interpreter_needs(monkeypatch):
    monkeypatch.setenv("SPECTO_TEST_SECRET", "agent-chosen")
    env = minimal_env()
    assert "SPECTO_TEST_SECRET" not in env
    assert set(env) <= set(MINIMAL_ENV_KEYS)
    script = "import os, sys; sys.stdout.write(','.join(sorted(os.environ)))"
    run = run_child(
        [sys.executable, "-I", "-c", script],
        b"",
        timeout=30.0,
        out_cap=1 << 20,
        err_cap=1 << 20,
        env=env,
    )
    seen = set(run.stdout.decode().split(","))
    assert "SPECTO_TEST_SECRET" not in seen
    assert {k.upper() for k in seen if k} <= set(MINIMAL_ENV_KEYS)


def test_the_child_runs_in_the_folder_it_is_given(tmp_path: Path):
    script = "import os, sys; sys.stdout.write(os.getcwd())"
    run = run_child(
        [sys.executable, "-I", "-c", script],
        b"",
        timeout=30.0,
        out_cap=1 << 20,
        err_cap=1 << 20,
        cwd=str(tmp_path),
    )
    assert Path(run.stdout.decode()).resolve() == tmp_path.resolve()
