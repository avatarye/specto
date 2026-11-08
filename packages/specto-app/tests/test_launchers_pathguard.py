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
    "file://attacker/share/tool.exe",
]
HOSTILE_IDS = ["unc-backslashes", "unc-slashes", "nt-object", "long-unc", "file-uri"]

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows path forms")


class Reached(AssertionError):
    """A filesystem call or a launch the guard should have stopped."""


@pytest.fixture
def reached(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every filesystem call on a path naming the attacker is recorded and raises instead
    of running (a regression must never really reach the host); every launch at all is
    recorded and raises too. The list is checked as well, in case the code under test
    swallows the exception."""
    seen: list[str] = []

    def trap(owner: object, name: str, *, every: bool = False) -> None:
        orig = getattr(owner, name)

        def guarded(*args, **kwargs):
            if every or any("attacker" in str(a) for a in (*args, *kwargs.values())):
                seen.append(f"{name}{args}")
                raise Reached(f"{name}{args}")
            return orig(*args, **kwargs)

        monkeypatch.setattr(owner, name, guarded)

    for name in ("stat", "lstat", "is_file", "is_dir", "exists", "resolve", "open"):
        trap(Path, name)
    for name in ("stat", "lstat", "open", "listdir", "scandir"):
        trap(os, name)
    for name in ("isdir", "isfile", "exists", "realpath", "getsize"):
        trap(os.path, name)
    trap(shutil, "which")
    trap(subprocess, "Popen", every=True)
    for name in ("spawn_program", "start_file", "reveal"):
        trap(launcher_run, name, every=True)
    return seen


def book_with(tmp_path: Path, machine_toml: str, project_toml: str | None = None) -> LauncherBook:
    machine = tmp_path / "user" / "launchers.toml"
    machine.parent.mkdir(parents=True)
    machine.write_text(machine_toml, encoding="utf-8")
    (tmp_path / "proj" / ".specto").mkdir(parents=True)
    (tmp_path / "proj" / "s.blend").write_bytes(b"x")
    if project_toml is not None:
        (tmp_path / "proj" / ".specto" / "launchers.toml").write_text(
            project_toml, encoding="utf-8"
        )
    return LauncherBook(tmp_path / "proj", machine_path=machine, log=lambda _m: None)


def refused_words(message: str) -> bool:
    return "network" in message or "device" in message


@pytest.mark.parametrize("command", HOSTILE, ids=HOSTILE_IDS)
@pytest.mark.parametrize("where", ["machine", "project"])
def test_a_network_command_never_runs_or_is_looked_for(tmp_path, reached, command, where):
    toml = f"[[launcher]]\nid = \"b\"\ncommand = '{command}'\n"
    book = (
        book_with(tmp_path, toml)
        if where == "machine"
        else book_with(tmp_path, "", project_toml=toml)
    )
    with pytest.raises(InvalidArgs) as e:
        book.launch("b", ["s.blend"])
    assert refused_words(e.value.message), e.value.message
    assert "launchers.toml:3" in e.value.fix
    if where == "project":
        assert ".specto" in e.value.fix
    assert reached == []


def test_a_project_overriding_only_the_command_is_refused_and_named(tmp_path, reached):
    """The agent-writable project file overrides only ``command`` of a machine launcher by
    id: the merged launcher is refused, and the fix-it names the project's line, not the
    machine's."""
    exe = Path(sys.executable).as_posix()
    machine = (
        f'[[launcher]]\nid = "b"\nlabel = "Blender"\ncommand = \'{exe}\'\nmatch = ["*.blend"]\n'
    )
    project = "[[launcher]]\nid = \"b\"\ncommand = '\\\\attacker\\share\\tool.exe'\n"
    book = book_with(tmp_path, machine, project_toml=project)
    with pytest.raises(InvalidArgs) as e:
        book.launch("b", ["s.blend"])
    assert refused_words(e.value.message)
    assert f"{Path('.specto') / 'launchers.toml'}:3" in e.value.fix
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


@pytest.mark.parametrize("where", ["machine", "project"])
def test_a_network_cwd_is_refused_before_it_is_looked_for(tmp_path, reached, where):
    exe = Path(sys.executable).as_posix()
    launcher = f"[[launcher]]\nid = \"b\"\ncommand = '{exe}'\n"
    cwd = "cwd = '\\\\attacker\\share'\n"
    if where == "machine":
        book = book_with(tmp_path, launcher + cwd)
        line = 4
    else:
        book = book_with(tmp_path, launcher, project_toml='[[launcher]]\nid = "b"\n' + cwd)
        line = 3
    with pytest.raises(InvalidArgs) as e:
        book.launch("b", ["s.blend"])
    assert "cwd" in e.value.fix and f"launchers.toml:{line}" in e.value.fix
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
        assert refused_words(e.value.message), e.value.message
        assert "list_files" in e.value.fix
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
