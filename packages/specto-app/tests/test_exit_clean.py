"""A test run that leaves Python-made clipboard data behind exits cleanly (#172): with a
QMimeData from Python still on the clipboard at exit, the interpreter's teardown
segfaulted (exit 0xC0000005 after every test passed), so the qapp fixture clears the
clipboard. The paste test, run alone in a child pytest, is the case that crashed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtQuick")

HERE = Path(__file__).resolve().parent
PASTE = (
    "test_agent_prompt.py::"
    "test_a_formatted_paste_arrives_as_markdown_and_ctrl_shift_v_pastes_plain_text"
)


def test_a_run_that_pastes_rich_text_exits_cleanly():
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(HERE / PASTE), "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=HERE.parents[2],
    )
    assert "1 passed" in r.stdout, r.stdout[-2000:] + r.stderr[-1000:]
    assert r.returncode == 0, f"exit {r.returncode:#x} after the test passed"
