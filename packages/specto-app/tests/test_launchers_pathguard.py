"""SPEC-76: a launcher's command, its cwd and the paths it is asked to open pass
``specto_core.pathguard`` before any filesystem call or launch. On Windows asking whether
``\\\\host\\x`` exists connects to the host (and can hand it the user's credentials); a
network command would run a remote program. ``.specto/launchers.toml`` is agent-writable,
and the agent's ``launch`` names the paths."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from specto_app.launchers import menu as launcher_menu
from specto_app.launchers import run as launcher_run
from specto_app.launchers.book import LauncherBook, file_of
from specto_app.menu import MenuItemRef, MenuTarget
from specto_core.errors import InvalidArgs

#: a network or device path in each form a launcher file or a path argument can carry it
HOSTILE = [
    r"\\attacker\share\tool.exe",
    "//attacker/share/tool.exe",
    r"\??\UNC\attacker\share\tool.exe",
    r"\\?\UNC\attacker\share\tool.exe",
]
HOSTILE_IDS = ["unc-backslashes", "unc-slashes", "nt-object", "long-unc"]

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows path forms")


@pytest.fixture
def reached(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every filesystem call or launch on a path naming the attacker, recorded (the call
    still runs, so a swallowed exception cannot hide it); spawning anything at all is
    recorded too."""
    seen: list[str] = []

    def trap(owner: object, name: str) -> None:
        orig = getattr(owner, name)

        def recorded(*args, **kwargs):
            if any("attacker" in str(a) for a in (*args, *kwargs.values())):
                seen.append(f"{name}{args}")
            return orig(*args, **kwargs)

        monkeypatch.setattr(owner, name, recorded)

    for name in ("stat", "lstat", "is_file", "is_dir", "exists", "resolve", "open"):
        trap(Path, name)
    for name in ("stat", "lstat", "open", "listdir", "scandir"):
        trap(os, name)
    for name in ("isdir", "isfile", "exists", "realpath", "getsize"):
        trap(os.path, name)
    trap(shutil, "which")
    trap(subprocess, "Popen")
    for name in ("spawn_program", "start_file", "reveal"):
        monkeypatch.setattr(
            launcher_run, name, lambda *a, _n=name, **k: seen.append(f"{_n}{a}") or 1
        )
    return seen


def book_with(tmp_path: Path, toml: str) -> LauncherBook:
    machine = tmp_path / "user" / "launchers.toml"
    machine.parent.mkdir(parents=True)
    machine.write_text(toml, encoding="utf-8")
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "s.blend").write_bytes(b"x")
    return LauncherBook(tmp_path / "proj", machine_path=machine, log=lambda _m: None)


@pytest.mark.parametrize("command", HOSTILE, ids=HOSTILE_IDS)
def test_a_network_command_never_runs_or_is_looked_for(tmp_path, reached, command):
    book = book_with(tmp_path, f"[[launcher]]\nid = \"b\"\ncommand = '{command}'\n")
    with pytest.raises(InvalidArgs) as e:
        book.launch("b", ["s.blend"])
    assert "network" in e.value.message or "device" in e.value.message
    assert "launchers.toml:" in e.value.fix
    assert reached == []


def test_a_command_that_becomes_a_network_path_through_a_variable_never_runs(
    tmp_path, reached, monkeypatch
):
    monkeypatch.setenv("SPECTO_TEST_TOOLS", r"\\attacker\share")
    book = book_with(
        tmp_path, "[[launcher]]\nid = \"b\"\ncommand = '%SPECTO_TEST_TOOLS%\\tool.exe'\n"
    )
    with pytest.raises(InvalidArgs):
        book.launch("b", ["s.blend"])
    assert reached == []


def test_a_network_cwd_is_refused_before_it_is_looked_for(tmp_path, reached):
    exe = Path(sys.executable).as_posix()
    book = book_with(
        tmp_path, f"[[launcher]]\nid = \"b\"\ncommand = '{exe}'\ncwd = '\\\\attacker\\share'\n"
    )
    with pytest.raises(InvalidArgs) as e:
        book.launch("b", ["s.blend"])
    assert "cwd" in e.value.fix
    assert reached == []


@pytest.mark.parametrize("path", HOSTILE, ids=HOSTILE_IDS)
def test_a_network_path_to_open_never_reaches_the_filesystem(tmp_path, reached, path):
    exe = Path(sys.executable).as_posix()
    book = book_with(tmp_path, f"[[launcher]]\nid = \"b\"\ncommand = '{exe}'\n")
    for act in (
        lambda: book.launch("b", [path]),
        lambda: book.launch("system", [path]),
        lambda: book.show_in_explorer([path]),
        lambda: book.summary([path]),
        lambda: book.launch("b", [path + "#inner/1.jpg"]),
    ):
        with pytest.raises(InvalidArgs) as e:
            act()
        assert "network" in e.value.message or "device" in e.value.message
    assert file_of(path) == ""
    assert reached == []


@pytest.mark.parametrize("path", HOSTILE, ids=HOSTILE_IDS)
def test_the_menu_offers_nothing_for_a_network_item(tmp_path, reached, path):
    target = MenuTarget(
        view_id="fege", view_type="gallery", role="browser", kind="items",
        items=(MenuItemRef(key="x", path=path),), root=str(tmp_path),
    )  # fmt: skip
    assert launcher_menu.items(target) == []
    assert reached == []
