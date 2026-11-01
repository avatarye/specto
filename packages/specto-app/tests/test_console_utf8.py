"""tools/console_utf8.py: gui_drive and the v2 suite print what a cp1252 console cannot
encode (a status line's arrow, U+2193); redirected to a file on Windows that killed the
flow (#147). Both reconfigure stdout and stderr to UTF-8 at startup."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[3] / "tools"


def _print_arrow(reconfigure: bool) -> subprocess.CompletedProcess[bytes]:
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(TOOLS)!r})\n"
        "from console_utf8 import utf8_stdio\n"
        + ("utf8_stdio()\n" if reconfigure else "")
        + "print('down \\u2193')\n"
        "print('down \\u2193', file=sys.stderr)\n"
    )
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, timeout=60)


def test_a_redirected_cp1252_stdout_cannot_print_an_arrow_by_itself():
    assert _print_arrow(reconfigure=False).returncode != 0


def test_utf8_stdio_prints_non_ascii_to_a_redirected_cp1252_stdout_and_stderr():
    done = _print_arrow(reconfigure=True)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert done.stdout.decode("utf-8").strip() == "down \u2193"
    assert done.stderr.decode("utf-8").strip() == "down \u2193"


def test_the_tools_reconfigure_at_startup():
    for script in (TOOLS / "gui_drive.py", TOOLS / "gui_flows" / "v2_suite.py"):
        assert "utf8_stdio()" in script.read_text(encoding="utf-8"), script.name
