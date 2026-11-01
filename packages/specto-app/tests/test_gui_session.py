"""tools/gui_session.py: a GUI session runs the app on a scratch user settings dir, so a
flow cannot change the user's own model choice, recents or trust list (#89)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[3] / "tools" / "gui_session.py"
_spec = importlib.util.spec_from_file_location("gui_session", _PATH)
assert _spec is not None and _spec.loader is not None
gui_session = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gui_session)


def test_the_app_gets_a_scratch_user_dir_inside_the_project(tmp_path):
    env = gui_session.app_env(tmp_path / "proj", {"PATH": "x"})
    assert env["SPECTO_USER_DIR"] == str(tmp_path / "proj" / ".specto" / "gui-user")
    assert env["PATH"] == "x"
    assert env["PYTHONUNBUFFERED"] == "1"


def test_an_explicit_user_dir_wins(tmp_path):
    env = gui_session.app_env(tmp_path / "proj", {"SPECTO_USER_DIR": str(tmp_path / "mine")})
    assert env["SPECTO_USER_DIR"] == str(tmp_path / "mine")
