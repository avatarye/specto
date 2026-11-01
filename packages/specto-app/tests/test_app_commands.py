"""The running app serves every command provider, not just core (fresh interpreter, so
no other test's imports leak in)."""

from __future__ import annotations

import subprocess
import sys

CODE = """
import specto_app.app
from specto_core import Engine
names = {c["name"] for c in Engine(project=None).api_info()["commands"]}
missing = {"intake.preview", "snap", "shell.locate", "window.raise", "canvas.submit"} - names
print(sorted(missing))
"""


def test_app_registers_intake_and_app_commands():
    out = subprocess.run([sys.executable, "-c", CODE], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout + out.stderr
